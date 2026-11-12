-- Renovaciones por WhatsApp: operación adicional e idempotente.
-- No reemplaza la renovación de la tienda.
begin;

create table if not exists public.bot_renewals (
  id uuid primary key,
  user_id uuid not null,
  phone text not null,
  sale_id uuid not null,
  months integer not null,
  expected_expiration date,
  expected_total numeric(12,2) not null,
  result jsonb,
  created_at timestamptz not null default now()
);
alter table public.bot_renewals enable row level security;
revoke all on public.bot_renewals from public, anon, authenticated;
grant select, insert, update on public.bot_renewals to service_role;

create or replace function public.bot_renovar_venta(
  p_order_id uuid, p_user_id uuid, p_phone text, p_sale_id uuid,
  p_months integer, p_expected_expiration date, p_expected_total numeric
) returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_order public.bot_renewals%rowtype;
  v_customer public.clientes%rowtype;
  v_customer_id uuid;
  v_sale public.ventas%rowtype;
  v_customer_count integer;
  v_phone text := regexp_replace(p_phone, '^\+', '');
  v_local_phone text;
  v_service_id text;
  v_service_name text;
  v_kind text;
  v_base_price numeric;
  v_plan_enabled boolean;
  v_plan_allowed boolean;
  v_plan_price numeric;
  v_discount numeric := 0;
  v_price numeric;
  v_today date := (now() at time zone 'America/Lima')::date;
  v_new_expiration date;
  v_result jsonb;
begin
  if auth.role() is distinct from 'service_role' then
    raise exception 'Operación reservada al backend.' using errcode = '42501';
  end if;
  if p_order_id is null or p_user_id is null or p_sale_id is null
     or v_phone is null or v_phone !~ '^[1-9][0-9]{7,14}$'
     or p_months is null or p_months not in (1,3,6,12)
     or p_expected_total is null or p_expected_total <= 0 then
    raise exception 'Renovación inválida.';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('dadtv-bot-renewal:' || p_order_id::text, 0));
  select * into v_order from public.bot_renewals where id = p_order_id for update;
  if found then
    if v_order.user_id <> p_user_id or v_order.phone <> v_phone
       or v_order.sale_id <> p_sale_id or v_order.months <> p_months
       or v_order.expected_expiration is distinct from p_expected_expiration
       or v_order.expected_total <> p_expected_total then
      raise exception 'El identificador corresponde a otra renovación.' using errcode = '42501';
    end if;
    if v_order.result is null then
      raise exception 'La renovación necesita revisión.';
    end if;
    return v_order.result;
  end if;

  v_local_phone := case when v_phone ~ '^51[0-9]{9}$' then substring(v_phone from 3) else null end;
  select count(*), (array_agg(id))[1] into v_customer_count, v_customer_id
  from public.clientes
  where user_id = p_user_id and telefono in (v_phone, '+' || v_phone, v_local_phone);
  if v_customer_count <> 1 then
    raise exception 'El cliente debe estar registrado una sola vez con este número.';
  end if;
  select * into v_customer from public.clientes
  where id = v_customer_id and user_id = p_user_id for update;
  if v_customer.estado is not null and v_customer.estado <> 'activo' then
    raise exception 'La cuenta del cliente necesita revisión.';
  end if;

  select * into v_sale from public.ventas
  where id = p_sale_id and user_id = p_user_id and cliente_id = v_customer.id
    and liberada = false for update;
  if not found or v_sale.fecha_vencimiento is null or v_sale.fecha_vencimiento < v_today then
    raise exception 'La compra ya no está disponible para renovar desde WhatsApp.';
  end if;
  if v_sale.fecha_vencimiento is distinct from p_expected_expiration then
    raise exception 'La fecha de vencimiento cambió. Solicita una nueva cotización.';
  end if;

  select cs.servicio_id::text into v_service_id
  from public.cuentas_servicios cs
  where cs.id = v_sale.cuenta_servicio_id and cs.user_id = p_user_id;
  if v_service_id is null then
    raise exception 'No encontramos el servicio de esta compra.';
  end if;
  v_kind := case when v_sale.perfil_id is null then 'cuenta' else 'perfil' end;

  select s.nombre::text,
    case when v_kind = 'cuenta' then
      coalesce(cc.precio_cuenta, (select cs.precio_venta from public.cuentas_servicios cs
        where cs.user_id=p_user_id and cs.servicio_id::text=v_service_id limit 1))
    else
      coalesce(cc.precio_perfil, (select cs.precio_venta / 4 from public.cuentas_servicios cs
        where cs.user_id=p_user_id and cs.servicio_id::text=v_service_id limit 1))
    end,
    coalesce(cp.enable_plans, false),
    case p_months when 1 then cp.price_1m when 3 then cp.price_3m
      when 6 then cp.price_6m when 12 then cp.price_12m end,
    case p_months when 1 then coalesce(cp.enable_1m,true)
      when 3 then coalesce(cp.enable_3m,true)
      when 6 then coalesce(cp.enable_6m,true)
      when 12 then coalesce(cp.enable_12m,true) end
  into v_service_name, v_base_price, v_plan_enabled, v_plan_price, v_plan_allowed
  from public.servicios s
  left join public.config_catalogo cc on cc.user_id=p_user_id and cc.servicio_id::text=s.id::text
  left join public.config_planes cp on cp.user_id=p_user_id and cp.servicio_id::text=s.id::text
  where s.user_id=p_user_id and s.id::text=v_service_id limit 1;
  if not found or (not v_plan_enabled and p_months <> 1)
     or (v_plan_enabled and not coalesce(v_plan_allowed,true)) then
    raise exception 'El plan ya no está disponible.';
  end if;
  v_price := case when v_plan_enabled and v_plan_price is not null then v_plan_price
    else v_base_price * p_months end;
  if v_price is null or v_price <= 0 then
    raise exception 'No hay precio configurado para la renovación.';
  end if;
  if coalesce(v_customer.premium,false) then
    select coalesce(descuento_premium,0) into v_discount from public.config_general
    where user_id=p_user_id limit 1;
    v_discount := least(greatest(coalesce(v_discount,0),0),100);
    v_price := round(v_price * (1 - v_discount / 100), 0);
  end if;
  if v_price is distinct from p_expected_total then
    raise exception 'El precio cambió. Solicita una nueva cotización.';
  end if;
  if coalesce(v_customer.saldo,0) < v_price then
    raise exception 'Saldo insuficiente para renovar.';
  end if;

  v_new_expiration := (greatest(v_sale.fecha_vencimiento,v_today) + make_interval(months=>p_months))::date;
  insert into public.bot_renewals(id,user_id,phone,sale_id,months,expected_expiration,expected_total)
  values (p_order_id,p_user_id,v_phone,p_sale_id,p_months,p_expected_expiration,p_expected_total);
  update public.ventas set fecha_vencimiento=v_new_expiration
  where id=p_sale_id and user_id=p_user_id and cliente_id=v_customer.id;
  insert into public.pagos_ventas(user_id,venta_id,monto,fecha_pago,tipo)
  values (p_user_id,p_sale_id,v_price,now(),'renovacion');
  update public.clientes set saldo=coalesce(saldo,0)-v_price
  where id=v_customer.id and user_id=p_user_id;
  v_result := jsonb_build_object('ok',true,'sale_id',p_sale_id,
    'servicio',v_service_name,'months',p_months,'total',v_price,
    'previous_expiration',v_sale.fecha_vencimiento,'fecha_vencimiento',v_new_expiration,
    'previous_balance',coalesce(v_customer.saldo,0),
    'current_balance',coalesce(v_customer.saldo,0)-v_price);
  update public.bot_renewals set result=v_result where id=p_order_id;
  return v_result;
end;
$$;
revoke all on function public.bot_renovar_venta(uuid,uuid,text,uuid,integer,date,numeric) from public,anon,authenticated;
grant execute on function public.bot_renovar_venta(uuid,uuid,text,uuid,integer,date,numeric) to service_role;
commit;
