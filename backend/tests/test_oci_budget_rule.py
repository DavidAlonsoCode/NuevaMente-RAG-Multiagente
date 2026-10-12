"""Provisionamiento de metadata Budgets aislado; ningún envío ni IAM (#50)."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import oci
import pytest
from app.tools.oci_budget_rule import asegurar_regla

pytestmark = pytest.mark.unit


def test_crea_presupuesto_minimo_y_regla_sin_destinatarios():
    cliente = Mock()
    cliente.list_budgets.return_value = NS(data=[], has_next_page=False)
    cliente.create_budget.return_value = NS(
        data=NS(id="budget", display_name="nuevamente", amount=1, reset_period="MONTHLY")
    )
    cliente.list_alert_rules.return_value = NS(data=[])
    cliente.create_alert_rule.return_value = NS(data=NS(threshold=80, threshold_type="PERCENTAGE", recipients=None))
    resultado = asegurar_regla(cliente, "tenancy", "compartimento")
    presupuesto = cliente.create_budget.call_args.args[0]
    regla = cliente.create_alert_rule.call_args.args[1]
    assert presupuesto.targets == ["compartimento"] and presupuesto.amount == 1
    assert regla.type == "ACTUAL" and regla.threshold == 80 and regla.recipients is None
    assert resultado["regla_creada"] and not resultado["destinatarios_configurados"]
    assert isinstance(cliente.create_budget.call_args.kwargs["retry_strategy"], oci.retry.NoneRetryStrategy)


def test_presupuesto_y_regla_existentes_no_se_modifican():
    cliente = Mock()
    cliente.list_budgets.return_value = NS(
        data=[
            NS(
                id="b",
                targets=["compartimento"],
                target_compartment_id=None,
                display_name="Existente",
                amount=1,
                reset_period="MONTHLY",
            )
        ],
        has_next_page=False,
    )
    cliente.list_alert_rules.return_value = NS(
        data=[NS(type="ACTUAL", threshold_type="PERCENTAGE", threshold=60, recipients=None)]
    )
    assert not asegurar_regla(cliente, "tenancy", "compartimento")["regla_creada"]
    cliente.create_budget.assert_not_called()
    cliente.create_alert_rule.assert_not_called()


def test_inventario_incompleto_no_provisiona():
    cliente = Mock()
    cliente.list_budgets.return_value = NS(data=[], has_next_page=True)
    with pytest.raises(ValueError):
        asegurar_regla(cliente, "tenancy", "compartimento")
    cliente.create_budget.assert_not_called()
