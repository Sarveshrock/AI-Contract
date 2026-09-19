-- ContractLens 0005: private storage bucket with organisation-scoped object policies + realtime.
-- Object path convention: <org_id>/<contract_id>/<document_id>/<filename>

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'contracts', 'contracts', false, 52428800,
  array['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']
)
on conflict (id) do update
  set public = false, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists contracts_objects_select on storage.objects;
create policy contracts_objects_select on storage.objects for select to authenticated
  using (bucket_id = 'contracts' and public.is_org_member(((storage.foldername(name))[1])::uuid));

drop policy if exists contracts_objects_insert on storage.objects;
create policy contracts_objects_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'contracts' and public.has_org_role(((storage.foldername(name))[1])::uuid,
    array['owner', 'admin', 'legal_reviewer', 'contract_manager', 'member']));

drop policy if exists contracts_objects_delete on storage.objects;
create policy contracts_objects_delete on storage.objects for delete to authenticated
  using (bucket_id = 'contracts' and public.has_org_role(((storage.foldername(name))[1])::uuid,
    array['owner', 'admin', 'legal_reviewer', 'contract_manager']));

-- Realtime (optional; the desktop client currently polls). Enables push for alerts/runs/documents.
do $$
declare t text;
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    foreach t in array array['alerts', 'analysis_runs', 'documents'] loop
      begin
        execute format('alter publication supabase_realtime add table public.%I', t);
      exception when duplicate_object then null;
      end;
    end loop;
  end if;
end;
$$;
