"""OpenCMKG 派生 JSON 的污染回归测试。"""
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_dataset_tables.py"


def _builder():
    spec = importlib.util.spec_from_file_location("build_dataset_tables", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_strings(item)


def test_clean_value_rejects_confirmed_pollution_without_broad_tail_filter():
    b = _builder()
    for value in ["武汉)医药", "河北)", "红斑(边界清楚", "仝超"]:
        assert b._clean_value(value) == ""

    assert b._clean_value("N动脉瘤", field="disease") == ""
    assert b._clean_value("中枢神经抑制药", field="symptoms") == ""
    assert b._clean_value("HBV与HCV", field="symptoms") == ""
    assert b._clean_value("白细胞计数(WBC)", field="symptoms") == ""

    # 这些是完整医学短语，不能因为末字碰巧是「等/现」而误删。
    assert b._clean_value("肌纤维大小不等", field="symptoms")
    assert b._clean_value("胃肠道排空表现", field="symptoms")


def test_builders_apply_relation_specific_cleaning(tmp_path):
    b = _builder()
    triples = tmp_path / "triples.txt"
    triples.write_text(
        "N动脉瘤,disease_belong_department,普外科\n"
        "正常病,disease_belong_department,内科\n"
        "正常病,disease_has_symptom,中枢神经抑制药\n"
        "正常病,disease_has_symptom,胸痛\n"
        "正常病,disease_need_check,白细胞计数(WBC)\n"
        "正常病,disease_recommand_drug,武汉)医药\n"
        "正常病,disease_need_treatment,支持性治疗\n",
        encoding="utf-8",
    )

    disease_table = b.build_diseases(str(triples))["diseases"]
    assert "N动脉瘤" not in disease_table
    assert disease_table["正常病"]["symptoms"] == ["胸痛"]
    assert disease_table["正常病"]["checks"] == ["白细胞计数(WBC)"]
    assert "drugs" not in disease_table["正常病"]

    symptom_table = b.build(str(triples))["symptoms"]
    assert set(symptom_table) == {"胸痛"}


def test_generated_medical_json_has_no_confirmed_pollution():
    b = _builder()
    disease_data = json.loads(
        (ROOT / "demo" / "disease_facts.json").read_text(encoding="utf-8")
    )
    symptom_data = json.loads(
        (ROOT / "demo" / "symptom_departments.json").read_text(encoding="utf-8")
    )
    all_values = list(_all_strings(disease_data)) + list(_all_strings(symptom_data))

    assert not [
        value for value in all_values
        if value.count("(") != value.count(")")
        or value.count("（") != value.count("）")
    ]
    assert not [
        name for name in disease_data["diseases"]
        if name in b._DISEASE_BLACKLIST
    ]
    for polluted in [
        "仝超", "自动Babin", "鼻Z", "食管左壁形成压足E", "皮肌炎Gott",
    ]:
        assert polluted not in all_values

    symptom_values = set(symptom_data["symptoms"])
    for card in disease_data["diseases"].values():
        symptom_values.update(card.get("symptoms") or [])
    for polluted in [
        "中枢神经抑制药", "HBV与HCV", "白细胞计数(WBC)",
    ]:
        assert polluted not in symptom_values
