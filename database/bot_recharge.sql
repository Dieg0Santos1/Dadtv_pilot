-- Función adicional para la recarga del bot. No modifica verify-receipt,
-- recargar_saldo ni los permisos usados por la tienda web.
-- Prueba limitada al número autorizado, con acreditación automática máxima S/100.

begin;

do $$
begin
  if to_regclass('public.recargas_hash_uidx') is null
     or to_regclass('public.recargas_operation_uidx') is null then
    raise exception 'Faltan índices únicos de comprobante u operación en recargas; no instalar el bot.';
  end if;
end;
$$;

create or replace function public.bot_aprobar_recarga(
  p_recarga_id uuid,
  p_user_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_recarga public.recargas%rowtype;
  v_previous numeric;
  v_current numeric;
begin
  if auth.role() is distinct from 'service_role' then
    raise exception 'Solo el backend puede aprobar recargas del bot.' using errcode = '42501';
  end if;

  select * into v_recarga
  from public.recargas
  where id = p_recarga_id and tenant_id = p_user_id
    and receipt_path like (cliente_id::text || '/whatsapp-%')
  for update;
  if not found then
    raise exception 'Recarga del bot no encontrada.';
  end if;
  if v_recarga.status = 'approved' then
    return jsonb_build_object('ok', true, 'already_approved', true,
      'previous_balance', v_recarga.analysis_json->'previous_balance',
      'current_balance', v_recarga.analysis_json->'current_balance');
  end if;
  if v_recarga.status <> 'pending'
    or coalesce(v_recarga.alias_ok, false) is false
    or coalesce(v_recarga.receiver_ok, false) is false
    or coalesce(v_recarga.time_ok, false) is false
    or coalesce(v_recarga.amount_ok, false) is false
    or v_recarga.operation_id is null
    or v_recarga.monto_declarado < 20
    or v_recarga.monto_declarado > 100
    or v_recarga.monto_detectado is distinct from v_recarga.monto_declarado
    or v_recarga.analysis_json->>'confidence' <> 'high'
    or v_recarga.analysis_json->>'receiver_phone_ok' <> 'true'
  then
    raise exception 'El comprobante necesita revisión manual.';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('bot-receipt-hash:' || v_recarga.hash, 0));
  perform pg_advisory_xact_lock(hashtextextended(
    'bot-receipt-operation:' || v_recarga.tenant_id::text || ':' || v_recarga.operation_id, 0));

  -- Los índices únicos de recargas también impiden reutilizar el hash o
  -- código de operación. Esta comprobación cubre filas ya aprobadas.
  if exists (
    select 1 from public.recargas other
    where other.id <> v_recarga.id and other.status = 'approved'
      and (other.hash = v_recarga.hash
        or (other.tenant_id = v_recarga.tenant_id and other.operation_id = v_recarga.operation_id))
  ) then
    raise exception 'Comprobante u operación ya acreditados.';
  end if;

  select coalesce(saldo, 0) into v_previous
  from public.clientes
  where id = v_recarga.cliente_id and user_id = p_user_id
  for update;
  if not found then
    raise exception 'Cliente no encontrado.';
  end if;
  v_current := v_previous + v_recarga.monto_declarado;
  update public.clientes set saldo = v_current
  where id = v_recarga.cliente_id and user_id = p_user_id;
  update public.recargas
  set status = 'approved',
      analysis_json = coalesce(analysis_json, '{}'::jsonb) || jsonb_build_object(
        'previous_balance', v_previous, 'current_balance', v_current)
  where id = v_recarga.id;
  return jsonb_build_object('ok', true, 'already_approved', false,
    'previous_balance', v_previous, 'current_balance', v_current);
end;
$$;

revoke all on function public.bot_aprobar_recarga(uuid, uuid) from public, anon, authenticated;
grant execute on function public.bot_aprobar_recarga(uuid, uuid) to service_role;

commit;
