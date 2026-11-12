-- PREPARAR EN STAGING. No se ejecuta automáticamente.
-- No cambia tablas ni la lógica de la tienda; permite al backend llamar la compra existente.
-- Requiere procesar_compra_tienda(uuid,uuid,jsonb) de DadTvStore.
begin;
create table if not exists public.bot_orders (
  id uuid primary key,
  user_id uuid not null,
  phone text not null,
  items jsonb not null,
  expected_total numeric(12,2) not null,
  result jsonb,
  created_at timestamptz not null default now()
);
alter table public.bot_orders enable row level security;
revoke all on public.bot_orders from public, anon, authenticated;
grant select, insert, update on public.bot_orders to service_role;

create or replace function public.bot_procesar_compra(
  p_order_id uuid, p_user_id uuid, p_phone text, p_items jsonb, p_expected_total numeric
) returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_order public.bot_orders%rowtype;
  v_cliente_id uuid;
  v_customer_count integer;
  v_result jsonb;
  v_phone text := regexp_replace(p_phone, '^\+', '');
  v_local_phone text;
begin
  if auth.role() is distinct from 'service_role' then
    raise exception 'Operación reservada al backend.' using errcode = '42501';
  end if;
  if p_order_id is null or p_user_id is null or v_phone is null or v_phone !~ '^[1-9][0-9]{7,14}$'
     or p_expected_total is null or p_expected_total <= 0 then
    raise exception 'Pedido inválido.';
  end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then
    raise exception 'El pedido debe contener productos.';
  end if;
  if jsonb_array_length(p_items) < 1 or jsonb_array_length(p_items) > 20 then
    raise exception 'Cantidad de productos inválida.';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('dadtv-bot-order:' || p_order_id::text, 0));
  select * into v_order from public.bot_orders where id = p_order_id for update;
  if found then
    if v_order.user_id <> p_user_id or v_order.phone <> v_phone
       or v_order.items <> p_items or v_order.expected_total <> p_expected_total then
      raise exception 'El identificador corresponde a otro pedido.' using errcode = '42501';
    end if;
    if v_order.result is null then
      raise exception 'El pedido necesita revisión.';
    end if;
    return v_order.result;
  end if;

  v_local_phone := case when v_phone ~ '^51[0-9]{9}$' then substring(v_phone from 3) else null end;
  select count(*), (array_agg(id))[1] into v_customer_count, v_cliente_id
  from public.clientes
  where user_id = p_user_id and telefono in (v_phone, '+' || v_phone, v_local_phone);
  if v_customer_count <> 1 then
    raise exception 'El cliente debe estar registrado una sola vez con este número.';
  end if;
  if exists (select 1 from public.clientes where id=v_cliente_id and user_id=p_user_id
             and estado is not null and estado <> 'activo') then
    raise exception 'La cuenta del cliente necesita revisión.';
  end if;

  insert into public.bot_orders(id,user_id,phone,items,expected_total)
  values (p_order_id,p_user_id,v_phone,p_items,p_expected_total);
  v_result := public.procesar_compra_tienda(p_user_id,v_cliente_id,p_items);
  if coalesce((v_result->>'ok')::boolean, false) is false
     or (v_result->>'total')::numeric is distinct from p_expected_total then
    -- Revertir venta, saldo y pedido si el precio ya no coincide con la cotización.
    raise exception 'El total cambió. Solicita una cotización nueva.';
  end if;
  update public.bot_orders set result=v_result where id=p_order_id;
  return v_result;
end;
$$;
revoke all on function public.bot_procesar_compra(uuid,uuid,text,jsonb,numeric) from public, anon, authenticated;
grant execute on function public.bot_procesar_compra(uuid,uuid,text,jsonb,numeric) to service_role;
grant execute on function public.procesar_compra_tienda(uuid,uuid,jsonb) to service_role;
commit;
