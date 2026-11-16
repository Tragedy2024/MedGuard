"""无 OpenCMKG 派生数据时的合成回退。

新克隆默认没有 `disease_facts.json` / `symptom_departments.json`
（上游许可不允许随仓库分发）。为了让「开箱即跑」成立，`dataset_kb`
在**主文件缺失**时回退到随仓库提交的 `.synthetic.json`：

- 合成文件必须明确标注「合成演示数据（非临床）」，不得冒充 OpenCMKG；
- 主文件存在时永远优先，哪怕合成文件也在；
- 主文件存在但损坏时**不**回退（避免把数据故障伪装成正常），
  仍按原行为返回空表。
"""
import json

import pytest

from backend import config, dataset_kb


@pytest.fixture(autouse=True)
def _clean_cache():
    dataset_kb.reset()
    yield
    dataset_kb.reset()


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _real_disease_payload(source="OpenCMKG", review="未经临床复核"):
    return {
        "_meta": {"source": source, "review_status": review},
        "diseases": {
            "帕金森病": {
                "symptoms": ["震颤", "运动迟缓"],
                "checks": ["头颅 MRI"],
                "dept": "神经内科",
            }
        },
    }


def _synthetic_disease_payload():
    return {
        "_meta": {"source": "合成演示数据（非临床）", "review_status": "合成演示"},
        "diseases": {
            "帕金森病": {
                "symptoms": ["震颤"],
                "checks": ["头颅 MRI"],
                "dept": "神经内科",
            }
        },
    }


def _synthetic_symptom_payload():
    return {
        "_meta": {"source": "合成演示数据（非临床）", "review_status": "合成演示"},
        "symptoms": {
            "睡眠障碍": {
                "total": 10,
                "depts": [["精神科", 4], ["神经内科", 3]],
            }
        },
        "alias_map": {"auto": {}, "manual": {"睡不踏实": "睡眠障碍"}},
    }


def test_missing_primary_falls_back_to_synthetic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO_DIR", str(tmp_path))
    _write(tmp_path / "disease_facts.synthetic.json", _synthetic_disease_payload())

    info = dataset_kb.lookup_disease("帕金森病")

    assert info is not None
    assert info["facts"]["dept"] == "神经内科"
    assert "合成演示数据" in dataset_kb.source_note(dataset_kb.DISEASE_FILE)


def test_primary_file_wins_over_synthetic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO_DIR", str(tmp_path))
    _write(tmp_path / "disease_facts.json", _real_disease_payload())
    _write(tmp_path / "disease_facts.synthetic.json", _synthetic_disease_payload())

    info = dataset_kb.lookup_disease("帕金森病")

    assert info is not None
    assert dataset_kb.source_note(dataset_kb.DISEASE_FILE).startswith("OpenCMKG")


def test_corrupt_primary_does_not_fall_back(tmp_path, monkeypatch):
    """主文件存在但损坏时不能悄悄换成合成数据。"""
    monkeypatch.setattr(config, "DEMO_DIR", str(tmp_path))
    (tmp_path / "disease_facts.json").write_text("{not json", encoding="utf-8")
    _write(tmp_path / "disease_facts.synthetic.json", _synthetic_disease_payload())

    assert dataset_kb.lookup_disease("帕金森病") is None
    assert dataset_kb.source_note(dataset_kb.DISEASE_FILE) == ""


def test_symptom_missing_primary_falls_back_to_synthetic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO_DIR", str(tmp_path))
    _write(tmp_path / "symptom_departments.synthetic.json",
           _synthetic_symptom_payload())

    triage = dataset_kb.lookup_symptom("睡不踏实")

    assert triage is not None
    assert triage["matched"] == "睡眠障碍"
    assert triage["candidates"][0]["department"] == "精神科"
    assert "合成演示数据" in dataset_kb.source_note(dataset_kb.SYMPTOM_FILE)


def test_no_files_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO_DIR", str(tmp_path))

    assert dataset_kb.lookup_disease("帕金森病") is None
    assert dataset_kb.lookup_symptom("失眠") is None
    assert dataset_kb.disease_names() == []
