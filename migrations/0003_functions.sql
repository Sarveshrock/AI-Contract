-- ContractLens 0003: authorisation helpers, audit trigger, onboarding RPCs.
-- All SECURITY DEFINER functions pin search_path and are callable only by authenticated users.

create or replace function public.is_org_member(p_org uuid)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select exists (
    select 1 from public.organization_members m
    where m.org_id = p_org and m.user_id = auth.uid()
  );
$$;

create or replace function public.has_org_role(p_org uuid, p_roles text[])
returns boolean
language sql stable security definer
set search_path = public
as $$
  select exists (
    select 1 from public.organization_members m
    where m.org_id = p_org and m.user_id = auth.uid() and m.role = any (p_roles)
  );
$$;

create or replace function public.shares_org_with(p_user uuid)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members mine
    join public.organization_members theirs on theirs.org_id = mine.org_id
    where mine.user_id = auth.uid() and theirs.user_id = p_user
  );
$$;

-- Row-level audit trail (before/after images) for tables flagged `audited` in the models.
create or replace function public.audit_row_change()
returns trigger
language plpgsql security definer
set search_path = public
as $$
declare
  v_row   jsonb;
  v_org   uuid;
  v_email text;
begin
  if tg_op = 'DELETE' then v_row := to_jsonb(old); else v_row := to_jsonb(new); end if;
  v_org := (v_row ->> 'org_id')::uuid;
  if v_org is null then return null; end if;
  select email into v_email from public.profiles where id = auth.uid();
  insert into public.audit_logs (org_id, actor_id, actor_email, action, entity_type, entity_id, before, after, metadata)
  values (
    v_org, auth.uid(), v_email, tg_table_name || '.' || lower(tg_op), tg_table_name, (v_row ->> 'id')::uuid,
    case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end,
    case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end,
    jsonb_build_object('source', 'row_trigger')
  );
  return null;
end;
$$;

-- Application-level audit events (login, AI queries, external actions...). Membership is verified.
create or replace function public.log_audit_event(
  p_org uuid, p_action text, p_entity_type text, p_entity_id uuid, p_metadata jsonb default '{}'::jsonb
) returns void
language plpgsql security definer
set search_path = public
as $$
declare v_email text;
begin
  if not public.is_org_member(p_org) then
    raise exception 'not a member of organisation' using errcode = '42501';
  end if;
  select email into v_email from public.profiles where id = auth.uid();
  insert into public.audit_logs (org_id, actor_id, actor_email, action, entity_type, entity_id, metadata)
  values (p_org, auth.uid(), v_email, p_action, p_entity_type, p_entity_id, coalesce(p_metadata, '{}'::jsonb));
end;
$$;

-- Create the profile row when a Supabase Auth user is created.
create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, full_name)
  values (new.id, coalesce(new.email, ''), new.raw_user_meta_data ->> 'full_name')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Atomically create an organisation and make the caller its owner.
create or replace function public.create_organization(p_name text, p_slug text)
returns uuid
language plpgsql security definer
set search_path = public
as $$
declare v_org uuid;
begin
  if auth.uid() is null then
    raise exception 'authentication required' using errcode = '28000';
  end if;
  insert into public.organizations (name, slug) values (p_name, p_slug) returning id into v_org;
  insert into public.organization_members (org_id, user_id, role) values (v_org, auth.uid(), 'owner');
  update public.profiles set default_org_id = coalesce(default_org_id, v_org) where id = auth.uid();
  return v_org;
end;
$$;

revoke all on function public.is_org_member(uuid) from public, anon;
revoke all on function public.has_org_role(uuid, text[]) from public, anon;
revoke all on function public.shares_org_with(uuid) from public, anon;
revoke all on function public.log_audit_event(uuid, text, text, uuid, jsonb) from public, anon;
revoke all on function public.create_organization(text, text) from public, anon;
grant execute on function public.is_org_member(uuid) to authenticated;
grant execute on function public.has_org_role(uuid, text[]) to authenticated;
grant execute on function public.shares_org_with(uuid) to authenticated;
grant execute on function public.log_audit_event(uuid, text, text, uuid, jsonb) to authenticated;
grant execute on function public.create_organization(text, text) to authenticated;

-- Add an existing user (by e-mail) to an organisation. Callable only by that organisation's owner/admin.
-- Owners can only be granted by owners. Users must have signed up (a profile row exists).
create or replace function public.add_org_member_by_email(p_org uuid, p_email text, p_role text)
returns uuid
language plpgsql security definer
set search_path = public
as $$
declare
  v_user   uuid;
  v_member uuid;
begin
  if not public.has_org_role(p_org, array['owner', 'admin']) then
    raise exception 'administrator role required' using errcode = '42501';
  end if;
  if p_role = 'owner' and not public.has_org_role(p_org, array['owner']) then
    raise exception 'only an owner can grant the owner role' using errcode = '42501';
  end if;
  select id into v_user from public.profiles where lower(email) = lower(p_email);
  if v_user is null then
    raise exception 'no user with that e-mail has signed up' using errcode = 'P0002';
  end if;
  insert into public.organization_members (org_id, user_id, role) values (p_org, v_user, p_role)
  on conflict (org_id, user_id) do update set role = excluded.role
    where public.organization_members.role <> 'owner' or public.has_org_role(p_org, array['owner'])
  returning id into v_member;
  return v_member;
end;
$$;

revoke all on function public.add_org_member_by_email(uuid, text, text) from public, anon;
grant execute on function public.add_org_member_by_email(uuid, text, text) to authenticated;
