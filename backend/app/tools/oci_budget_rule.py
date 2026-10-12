"""Regla Budgets del compartimento: idempotente, sin IAM ni destinatarios implícitos."""

import oci


def asegurar_regla(cliente, tenancy, compartimento):
    pagina = cliente.list_budgets(tenancy, limit=100, retry_strategy=oci.retry.NoneRetryStrategy())
    if pagina.has_next_page:
        raise ValueError("Completar inventario Budgets antes de provisionar")
    compatibles = [
        b for b in pagina.data if compartimento in (b.targets or []) or b.target_compartment_id == compartimento
    ]
    if len(compatibles) > 1:
        raise ValueError("Más de un presupuesto compatible; revisar manualmente")
    presupuesto = compatibles[0] if compatibles else None
    if presupuesto is None:
        # OCI exige un mínimo de 1 unidad monetaria. Es sólo un umbral de
        # observación, nunca un permiso para gastar ni un corte de consumo.
        presupuesto = cliente.create_budget(
            oci.budget.models.CreateBudgetDetails(
                compartment_id=tenancy,
                target_type="COMPARTMENT",
                targets=[compartimento],
                display_name="nuevamente-cost-governance",
                amount=1,
                reset_period="MONTHLY",
                description="Alerta de facturación NuevaMente; los límites de aplicación mantienen costo cero.",
            ),
            retry_strategy=oci.retry.NoneRetryStrategy(),
        ).data
    reglas = cliente.list_alert_rules(presupuesto.id, retry_strategy=oci.retry.NoneRetryStrategy()).data
    existentes = [
        r for r in reglas if r.type == "ACTUAL" and r.threshold_type == "PERCENTAGE" and 0 < r.threshold <= 80
    ]
    creada = not existentes
    if creada:
        regla = cliente.create_alert_rule(
            presupuesto.id,
            oci.budget.models.CreateAlertRuleDetails(
                display_name="nuevamente-actual-80",
                type="ACTUAL",
                threshold_type="PERCENTAGE",
                threshold=80,
                description="Alerta al 80%; no sustituye cuotas y presupuestos de aplicación.",
            ),
            retry_strategy=oci.retry.NoneRetryStrategy(),
        ).data
    else:
        regla = existentes[0]
    return {
        "budget": presupuesto.display_name,
        "monto": presupuesto.amount,
        "periodo": presupuesto.reset_period,
        "umbral": regla.threshold,
        "tipo_umbral": regla.threshold_type,
        "regla_creada": creada,
        "destinatarios_configurados": bool(regla.recipients),
    }
