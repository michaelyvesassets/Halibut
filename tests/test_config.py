import pytest

from halibut.config import CONTROL, ConfigError, config_hash, load_config, parse_config
from halibut.interventions import ProductChange, SeedAdopters

from .conftest import EXAMPLE


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.product_ids == ("coffee", "matcha")
    assert cfg.population.size == 5000
    assert cfg.weeks == 52
    assert len(cfg.seeds) == 20 and len(set(cfg.seeds)) == 20
    assert cfg.scenario_names == ["control", "price_war", "distribution_push",
                                  "influencer_campaign", "combined"]
    assert cfg.scenario(CONTROL).interventions == []
    assert isinstance(cfg.scenario("price_war").interventions[0], ProductChange)
    assert isinstance(cfg.scenario("influencer_campaign").interventions[0], SeedAdopters)


def test_hash_is_stable_and_sensitive(raw):
    h1 = config_hash(parse_config(raw))
    assert h1 == config_hash(parse_config(raw))
    raw["decision"]["temperature"] = 0.9
    assert config_hash(parse_config(raw)) != h1


def test_defaults_are_part_of_hash(raw):
    del raw["decision"]["quality_weight"]
    explicit = dict(raw, decision={**raw["decision"], "quality_weight": 1.0})
    assert config_hash(parse_config(raw)) == config_hash(parse_config(explicit))


def test_control_added_when_missing(raw):
    raw["scenarios"] = [s for s in raw["scenarios"] if s["name"] != "control"]
    cfg = parse_config(raw)
    assert cfg.scenario_names[0] == CONTROL
    assert cfg.scenario(CONTROL).interventions == []


def test_control_with_interventions_rejected(raw):
    raw["scenarios"][0]["interventions"] = raw["scenarios"][1]["interventions"]
    with pytest.raises(ConfigError, match="control"):
        parse_config(raw)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d.update(schema_version="9.9"), "schema_version"),
        (lambda d: d["experiment"].update(bogus=1), "unknown keys"),
        (lambda d: d["population"]["segments"][0]["initial_product_shares"].update(none=0.5), "sum to 1"),
        (lambda d: d["population"]["segments"][0]["initial_product_shares"].update(tea=0.0), "unknown product"),
        (lambda d: d["network"].update(k=5), "even"),
        (lambda d: d["products"][1].update(availability=1.5), "availability"),
        (lambda d: d["products"][1].update(id="coffee"), "unique"),
        (lambda d: d["products"][1].update(id="none"), "reserved"),
        (lambda d: d["population"]["segments"][0]["attributes"].pop("budget"), "budget"),
        (lambda d: d["population"]["segments"][0]["preferences"].update(matcha={"dist": "beta", "a": -1, "b": 1}), "beta"),
        (lambda d: d["scenarios"][1]["interventions"][0].update(product="tea"), "product"),
        (lambda d: d["scenarios"][1]["interventions"][0].update(start_week=999), "start_week"),
        (lambda d: d["scenarios"][1]["interventions"][0].update(attribute="colour"), "attribute"),
        (lambda d: d["decision"].update(temperature=-1), "temperature"),
        (lambda d: d["preference_drift"].update(enabled="yes"), "true or false"),
    ],
)
def test_invalid_configs_rejected(raw, mutate, match):
    mutate(raw)
    with pytest.raises(ConfigError, match=match):
        parse_config(raw)
