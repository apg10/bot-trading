"""ACT-C-001 / SLICE ENVIRONMENT CANÓNICO: pruebas de identidad y compatibilidad.

Verifica que `config.Environment` sea el mismo tipo canónico definido en
`models.state.Environment`, sin alterar valores wire, aliases, coerciones ni
la exclusión operativa de LIVE.
"""

from __future__ import annotations

import json
from enum import StrEnum

import pytest
from pydantic import TypeAdapter


class TestEnvironmentIdentity:
    """1. Identidad: config y models.state apuntan al mismo tipo."""

    def test_config_env_is_state_env(self):
        from src.config import Environment as ConfigEnv
        from src.models.state import Environment as StateEnv
        assert ConfigEnv is StateEnv, "config.Environment debe ser models.state.Environment"

    def test_init_reexports_same_type(self):
        from src import Environment as InitEnv
        from src.config import Environment as ConfigEnv
        assert InitEnv is ConfigEnv

    def test_is_str_enum(self):
        from src.models.state import Environment
        assert issubclass(Environment, StrEnum)


class TestEnvironmentMembers:
    """2. Miembros y valores wire conservados."""

    @pytest.fixture(autouse=True)
    def _env(self):
        from src.models.state import Environment
        return Environment

    def test_paper_member(self, _env):
        assert _env.PAPER.value == "paper"
        assert _env.PAPER == "paper"

    def test_exchange_demo_member(self, _env):
        assert _env.EXCHANGE_DEMO.value == "exchange_demo"
        assert _env.EXCHANGE_DEMO == "exchange_demo"

    def test_live_member_exists(self, _env):
        assert hasattr(_env, "LIVE")
        assert _env.LIVE.value == "live"

    def test_json_serialization_paper(self, _env):
        assert json.dumps(_env.PAPER) == '"paper"'
        assert TypeAdapter(_env).dump_json(_env.PAPER) == b'"paper"'

    def test_json_serialization_exchange_demo(self, _env):
        assert json.dumps(_env.EXCHANGE_DEMO) == '"exchange_demo"'
        assert TypeAdapter(_env).dump_json(_env.EXCHANGE_DEMO) == b'"exchange_demo"'

    def test_json_serialization_live(self, _env):
        assert json.dumps(_env.LIVE) == '"live"'
        assert TypeAdapter(_env).dump_json(_env.LIVE) == b'"live"'


class TestConfigParsing:
    """3. Config preserva parsing case-insensitive, aliases y fallback."""

    def test_paper_case_insensitive(self):
        from src.config import Config, Environment
        assert Config(env="pApEr").env is Environment.PAPER

    def test_exchange_demo_aliases(self):
        # Los aliases EXCHANGEDEMO y DEMO se resuelven en Config.__init__, no en el enum.
        from src.config import Config
        demo_via_alias = Config(env="EXCHANGEDEMO")
        assert demo_via_alias.env.value == "exchange_demo"
        assert Config(env="demo").env.value == "exchange_demo"
        demo_via_dash = Config(env="exchange-demo")
        assert demo_via_dash.env.value == "exchange_demo"

    def test_unknown_falls_back_to_paper(self):
        from src.config import Config, Environment
        assert Config(env="not-a-real-environment").env is Environment.PAPER


class TestLiveRejection:
    """4. LIVE sigue rechazado por Config."""

    def test_live_raises_in_config(self):
        from src.config import Config, Environment
        with pytest.raises(ValueError, match="LIVE"):
            Config(env="LIVE")

    def test_live_member_still_defined(self):
        from src.models.state import Environment
        assert hasattr(Environment, "LIVE")
        assert Environment.LIVE.value == "live"


class TestPydanticCoercion:
    """5. Pydantic sigue coerciendo strings a Environment."""

    def test_start_market_request_paper(self):
        from pydantic import BaseModel
        from src.models.state import Environment

        class Req(BaseModel):
            env: Environment

        r = Req(env="paper")
        assert r.env is Environment.PAPER
        assert r.env.value == "paper"

    def test_start_market_request_exchange_demo(self):
        from pydantic import BaseModel
        from src.models.state import Environment

        class Req(BaseModel):
            env: Environment

        r = Req(env="exchange_demo")
        assert r.env is Environment.EXCHANGE_DEMO

    def test_snapshot_environment_field(self):
        from pydantic import BaseModel
        from src.models.state import Environment, Candle

        class Snap(BaseModel):
            env: Environment
            candle: Candle

        snap = Snap(
            env="paper",
            candle=Candle(timestamp_ms=0, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0),
        )
        assert snap.env is Environment.PAPER

    def test_order_environment_field(self):
        from pydantic import BaseModel
        from src.models.state import Environment, OrderSide, OrderType

        class Ord(BaseModel):
            env: Environment

        o = Ord(env="exchange_demo")
        assert o.env is Environment.EXCHANGE_DEMO


class TestCapabilitiesContractUnchanged:
    """6. CapabilitiesContract no acepta LIVE."""

    def test_capabilities_environment_literal(self):
        from src.models.state import CapabilitiesContract
        import typing

        # Verificar que el campo environment es Literal["paper", "exchange_demo"]
        hints = typing.get_type_hints(CapabilitiesContract)
        env_hint = hints["environment"]
        # En Pydantic v2, los Literals se almacenan como __args__
        assert hasattr(env_hint, "__args__")
        values = set(env_hint.__args__)
        assert "paper" in values
        assert "exchange_demo" in values
        assert "live" not in values


class TestScenarioSnapshotUnchanged:
    """7. ScenarioSnapshot sigue exigiendo environment=Literal["paper"]."""

    def test_scenario_snapshot_environment_literal(self):
        from src.ai.scenario_models import ScenarioSnapshot
        import typing

        hints = typing.get_type_hints(ScenarioSnapshot)
        env_hint = hints["environment"]
        assert hasattr(env_hint, "__args__")
        values = set(env_hint.__args__)
        assert values == {"paper"}


class TestImportChain:
    """8. Cadena de imports intacta."""

    def test_src_init_exports_environment(self):
        import src
        assert hasattr(src, "Environment")
        from src import Environment as IE
        from src.models.state import Environment as SE
        assert IE is SE

    def test_config_module_has_environment(self):
        import importlib
        config_mod = importlib.import_module("src.config")
        assert hasattr(config_mod, "Environment")
        from src.config import Environment as CE
        from src.models.state import Environment as SE
        assert CE is SE
