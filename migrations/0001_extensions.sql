-- ContractLens 0001: shared helpers used by generated tables.
-- Apply migrations in numeric order (supabase db push, or psql -f).

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
