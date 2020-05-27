
import os

import pytest

from PyDSS.exceptions import InvalidConfiguration, InvalidParameter
from PyDSS.export_list_reader import ExportListProperty, ExportListReader, \
    LimitsFilter, StoreValuesType


EXPORT_LIST_FILE = "tests/data/exports/config.toml"
LEGACY_FILE = "tests/data/project/Scenarios/scenario1/ExportLists/ExportMode-byClass.toml"


def test_export_list_reader():
    reader = ExportListReader(EXPORT_LIST_FILE)
    assert reader.list_element_classes() == \
        ["Buses", "Circuits", "Lines", "Loads", "PVSystems", "Transformers"]
    assert reader.list_element_properties("Buses") == \
        ["Distance", "puVmagAngle"]
    prop = reader.get_element_property("Buses", "puVmagAngle")
    assert prop.store_values_type == StoreValuesType.ALL
    assert prop.should_store_name("bus2")
    assert prop.should_store_value(4.0)

    with pytest.raises(InvalidParameter):
        prop = reader.get_element_property("invalid", "Losses")
    with pytest.raises(InvalidParameter):
        prop = reader.get_element_property("Circuits", "invalid")


def test_export_list_reader__names():
    data = {"names": ["bus1", "bus2"]}
    export_prop = ExportListProperty("Buses", "puVmagAngle", data)
    assert export_prop.should_store_name("bus1")
    assert export_prop.should_store_name("bus2")
    assert not export_prop.should_store_name("bus3")

    with pytest.raises(InvalidConfiguration):
        ExportListProperty("Buses", "puVmagAngle", {"names": "bus1"})


def test_export_list_reader__name_regexes():
    data = {"name_regexes": [r"busFoo\d+", r"busBar\d+"]}
    export_prop = ExportListProperty("Buses", "puVmagAngle", data)
    assert not export_prop.should_store_name("bus1")
    assert export_prop.should_store_name("busFoo23")
    assert export_prop.should_store_name("busBar8")


def test_export_list_reader__name_and_name_regexes():
    data = {"names": ["bus1"], "name_regexes": [r"busFoo\d+"]}
    with pytest.raises(InvalidConfiguration):
        export_prop = ExportListProperty("Buses", "puVmagAngle", data)


def test_export_list_reader__limits():
    data = {"limits": [-1.0, 1.0], "limits_filter": LimitsFilter.OUTSIDE}
    export_prop = ExportListProperty("Buses", "puVmagAngle", data)
    assert export_prop.limits.min == -1.0
    assert export_prop.limits.max == 1.0
    assert export_prop.should_store_value(-2.0)
    assert export_prop.should_store_value(2.0)
    assert not export_prop.should_store_value(-0.5)
    assert not export_prop.should_store_value(0.5)

    data = {"limits": [-1.0, 1.0], "limits_filter": LimitsFilter.INSIDE}
    export_prop = ExportListProperty("Buses", "puVmagAngle", data)
    assert export_prop.limits.min == -1.0
    assert export_prop.limits.max == 1.0
    assert not export_prop.should_store_value(-2.0)
    assert not export_prop.should_store_value(2.0)
    assert export_prop.should_store_value(-0.5)
    assert export_prop.should_store_value(0.5)

    with pytest.raises(InvalidConfiguration):
        ExportListProperty("Buses", "puVmagAngle", {"limits": [1.0]})

    with pytest.raises(InvalidConfiguration):
        ExportListProperty("Buses", "puVmagAngle", {"limits": 1.0})


def test_export_list_reader__legacy_file():
    reader = ExportListReader(LEGACY_FILE)
    assert reader.list_element_classes() == \
        ["Buses", "Circuits", "Lines", "Loads", "PVSystems", "Storages", "Transformers"]
    assert reader.list_element_properties("Buses") == \
        ["Distance", "puVmagAngle"]
    prop = reader.get_element_property("Buses", "puVmagAngle")
    assert prop.store_values_type == StoreValuesType.ALL
    assert prop.should_store_name("bus2")
    assert prop.should_store_value(4.0)
    assert reader.publicationList == [
        "Loads Powers",
        "Storages Powers",
        "Circuits TotalPower",
        "Circuits LineLosses",
        "Circuits Losses",
        "Circuits SubstationLosses",
    ]
