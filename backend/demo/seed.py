"""建演示库（医院数据仿真版）。

所有数据均为虚构，命名一眼可辨：患者001 / TEST-000001 / 医生001。
用固定 seed（random.Random(42)）保证每次 seed 结果一致——测试与演示可复现。

锚点数据（预设查询依赖，勿删）：
- 患者 P001 有 lab 记录（test_name='血糖'）、medication 记录、imaging 记录
- 至少 3 位患者诊断为「2型糖尿病」（供跨域/派生演示）
- staff 表含 department='心内科' 的医生
"""
import os
import random
import sqlite3

TABLE_DDL = [
    """CREATE TABLE patients (
        patient_id   TEXT PRIMARY KEY,
        name         TEXT,          -- controlled
        phone        TEXT,          -- controlled
        birth_date   TEXT,          -- controlled
        address      TEXT,          -- controlled
        id_card      TEXT,          -- blocked
        gender       TEXT,
        blood_type   TEXT
    )""",
    """CREATE TABLE staff (
        staff_id   TEXT PRIMARY KEY,
        name       TEXT,            -- controlled
        phone      TEXT,            -- controlled
        title      TEXT,
        department TEXT,
        specialty  TEXT
    )""",
    """CREATE TABLE visits (
        visit_id    TEXT PRIMARY KEY,
        patient_id  TEXT NOT NULL,
        doctor_id   TEXT,
        department  TEXT,
        visit_type  TEXT,
        visit_date  TEXT
    )""",
    """CREATE TABLE clinical_records (
        record_id    TEXT PRIMARY KEY,
        visit_id     TEXT NOT NULL,
        patient_id   TEXT NOT NULL,
        record_type  TEXT NOT NULL,  -- diagnosis|medication|lab|imaging
        icd_code     TEXT,           -- blocked
        diagnosis_name TEXT,         -- blocked
        severity     TEXT,           -- controlled
        drug_name    TEXT,
        dosage       TEXT,
        frequency    TEXT,
        route        TEXT,
        test_name    TEXT,
        result_value TEXT,
        unit         TEXT,
        ref_range    TEXT,
        modality     TEXT,
        body_part    TEXT,
        findings     TEXT,           -- controlled
        impression   TEXT            -- controlled
    )""",
    """CREATE TABLE billing (
        bill_id        TEXT PRIMARY KEY,
        visit_id       TEXT NOT NULL,
        patient_id     TEXT NOT NULL,
        amount         REAL,         -- controlled
        insurance_type TEXT,
        item_name      TEXT,
        bill_date      TEXT
    )""",
]

# 锚点常量（queries.json 与测试依赖）
ANCHOR_PATIENT = "P001"
ANCHOR_LAB_TEST = "血糖"
DIABETES_DIAGNOSIS = "2型糖尿病"
CARDIOLOGY_DEPT = "心内科"

_DEPARTMENTS = [
    ("心内科", "心血管疾病"),
    ("心内科", "冠心病介入"),
    ("内分泌科", "糖尿病"),
    ("内分泌科", "甲状腺疾病"),
    ("呼吸科", "呼吸系统疾病"),
    ("消化内科", "消化系统疾病"),
    ("骨科", "创伤骨科"),
    ("神经内科", "神经系统疾病"),
]
_TITLES = ["主任医师", "副主任医师", "主治医师"]
_VISIT_TYPES = ["门诊", "住院", "急诊"]
_DIAGNOSES = ["2型糖尿病", "高血压", "支气管炎", "胃炎", "腰椎间盘突出", "上呼吸道感染"]
_ICD = ["E11.9", "I10", "J20", "K29", "M51", "J06"]
_LAB_TESTS = ["血糖", "血常规", "肝功能", "肾功能", "血脂", "尿常规"]
_MEDICATIONS = ["阿司匹林", "二甲双胍", "氨氯地平", "奥美拉唑", "布洛芬", "头孢呋辛"]
_MODALITIES = ["CT", "MRI", "X光", "B超"]
_BODY_PARTS = ["胸部", "腹部", "头部", "腰椎", "膝关节"]
_INSURANCE_TYPES = ["职工医保", "居民医保", "商业保险", "自费"]
_BILL_ITEMS = ["检验费", "药品费", "检查费", "治疗费", "住院费", "手术费"]


def build_database(db_path: str) -> None:
    """建 5 张表并灌入虚构数据。已存在的库文件会被重建。"""
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    con = sqlite3.connect(db_path)
    try:
        for ddl in TABLE_DDL:
            con.execute(ddl)
        _seed_patients_and_staff(con)
        _seed_visits_and_records(con)
        _seed_billing(con)
        con.commit()
    finally:
        con.close()


def _seed_patients_and_staff(con: sqlite3.Connection) -> None:
    rng = random.Random(42)

    for i in range(1, 31):
        seq = f"{i:03d}"
        con.execute(
            "INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"P{seq}", f"患者{seq}", f"TEST-PHONE-{seq}",
             f"19{rng.randint(60, 98):02d}-{rng.randint(1, 12):02d}-"
             f"{rng.randint(1, 28):02d}",
             f"测试地址-{seq}", f"TEST-{i:06d}",
             rng.choice(["男", "女"]), rng.choice(["A", "B", "AB", "O"])))

    for j, (dept, specialty) in enumerate(_DEPARTMENTS, start=1):
        seq = f"{j:03d}"
        con.execute(
            "INSERT INTO staff VALUES (?, ?, ?, ?, ?, ?)",
            (f"S{seq}", f"医生{seq}", f"TEST-PHONE-STAFF-{seq}",
             _TITLES[j % len(_TITLES)], dept, specialty))


def _visit_date(rng: random.Random) -> str:
    return (f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}")


def _seed_visits_and_records(con: sqlite3.Connection) -> None:
    rng = random.Random(42)

    staff_rows = con.execute("SELECT staff_id, department FROM staff").fetchall()
    staff_by_dept = {}
    for sid, dept in staff_rows:
        staff_by_dept.setdefault(dept, []).append(sid)

    patient_ids = [r[0] for r in con.execute(
        "SELECT patient_id FROM patients ORDER BY patient_id")]

    # 糖尿病锚点：保证至少 4 位患者有 2 型糖尿病诊断
    diabetes_patients = patient_ids[1:5]  # P002..P005

    visit_no = 0
    record_no = 0
    counted = {"diagnosis": 0, "medication": 0, "lab": 0, "imaging": 0}

    for pid in patient_ids:
        n_visits = rng.randint(2, 5)
        for _ in range(n_visits):
            visit_no += 1
            visit_id = f"V{visit_no:04d}"
            dept = rng.choice(list(staff_by_dept.keys()))
            doctor_id = rng.choice(staff_by_dept[dept])
            con.execute(
                "INSERT INTO visits VALUES (?, ?, ?, ?, ?, ?)",
                (visit_id, pid, doctor_id, dept,
                 rng.choice(_VISIT_TYPES), _visit_date(rng)))

            # P001 锚点：必须含 血糖 lab、medication、imaging
            if pid == ANCHOR_PATIENT and visit_no <= 2:
                if visit_no == 1:
                    _insert_record(con, pid, visit_id, "lab",
                                   test_name=ANCHOR_LAB_TEST,
                                   tester=rng)
                    _insert_record(con, pid, visit_id, "medication",
                                   tester=rng)
                else:
                    _insert_record(con, pid, visit_id, "imaging",
                                   tester=rng)
                counted["lab"] += 1
                counted["medication"] += 1
                counted["imaging"] += 1
                continue

            # 糖尿病锚点：为选定患者插入 diagnosis 记录
            if pid in diabetes_patients and counted["diagnosis"] < 4:
                _insert_record(con, pid, visit_id, "diagnosis",
                               diagnosis=DIABETES_DIAGNOSIS, tester=rng)
                counted["diagnosis"] += 1
                continue

            record_no += 1
            kind = rng.choice(["diagnosis", "lab", "medication", "imaging"])
            if kind == "diagnosis":
                _insert_record(con, pid, visit_id, "diagnosis", tester=rng)
            elif kind == "lab":
                _insert_record(con, pid, visit_id, "lab", tester=rng)
            elif kind == "medication":
                _insert_record(con, pid, visit_id, "medication", tester=rng)
            else:
                _insert_record(con, pid, visit_id, "imaging", tester=rng)


def _insert_record(con, pid: str, visit_id: str, kind: str, *,
                   tester: random.Random, diagnosis: str = None,
                   test_name: str = None) -> None:
    """插入一条 clinical_record。record_id 用 visit_id 派生，保证唯一。"""
    if kind == "diagnosis":
        name = diagnosis or tester.choice(_DIAGNOSES)
        icd = _ICD[_DIAGNOSES.index(name)] if name in _DIAGNOSES else "E11.9"
        con.execute(
            "INSERT INTO clinical_records (record_id, visit_id, patient_id,"
            " record_type, icd_code, diagnosis_name, severity)"
            " VALUES (?, ?, ?, 'diagnosis', ?, ?, ?)",
            (f"{visit_id}-D", visit_id, pid, icd, name,
             tester.choice(["轻", "中", "重"])))
    elif kind == "lab":
        name = test_name or tester.choice(_LAB_TESTS)
        con.execute(
            "INSERT INTO clinical_records (record_id, visit_id, patient_id,"
            " record_type, test_name, result_value, unit, ref_range)"
            " VALUES (?, ?, ?, 'lab', ?, ?, ?, ?)",
            (f"{visit_id}-L", visit_id, pid, name,
             f"{tester.randint(1, 120)}.{tester.randint(0, 9)}",
             tester.choice(["mmol/L", "g/L", "10^9/L"]),
             "参考范围: 见报告"))
    elif kind == "medication":
        con.execute(
            "INSERT INTO clinical_records (record_id, visit_id, patient_id,"
            " record_type, drug_name, dosage, frequency, route)"
            " VALUES (?, ?, ?, 'medication', ?, ?, ?, ?)",
            (f"{visit_id}-M", visit_id, pid, tester.choice(_MEDICATIONS),
             f"{tester.randint(1, 4)}片",
             tester.choice(["每日一次", "每日两次", "每日三次"]),
             tester.choice(["口服", "静脉注射"])))
    else:  # imaging
        con.execute(
            "INSERT INTO clinical_records (record_id, visit_id, patient_id,"
            " record_type, modality, body_part, findings, impression)"
            " VALUES (?, ?, ?, 'imaging', ?, ?, ?, ?)",
            (f"{visit_id}-I", visit_id, pid, tester.choice(_MODALITIES),
             tester.choice(_BODY_PARTS),
             "未见明显异常（测试数据）", "未见异常（测试数据）"))


def _seed_billing(con: sqlite3.Connection) -> None:
    rng = random.Random(42)

    # 优先给含 diagnosis 的 visit 配费用（跨域演示：诊断→费用可关联）
    diagnosis_visits = [r[0] for r in con.execute(
        "SELECT visit_id FROM clinical_records WHERE record_type='diagnosis'")]
    all_visits = [tuple(r) for r in con.execute(
        "SELECT visit_id, patient_id FROM visits ORDER BY visit_id")]
    visit_by_id = dict(all_visits)

    bill_no = 0
    target = 60

    for vid in diagnosis_visits:
        if bill_no >= target:
            break
        bill_no += 1
        _insert_bill(con, f"B{bill_no:04d}", vid, visit_by_id[vid], rng)

    for vid, pid in all_visits:
        if bill_no >= target:
            break
        if any(b[0] == vid for b in con.execute(
                "SELECT bill_id FROM billing WHERE visit_id=?", (vid,))):
            continue
        bill_no += 1
        _insert_bill(con, f"B{bill_no:04d}", vid, pid, rng)


def _insert_bill(con, bill_id: str, visit_id: str, pid: str,
                 rng: random.Random) -> None:
    con.execute(
        "INSERT INTO billing VALUES (?, ?, ?, ?, ?, ?, ?)",
        (bill_id, visit_id, pid,
         round(rng.uniform(50, 5000), 2),
         rng.choice(_INSURANCE_TYPES), rng.choice(_BILL_ITEMS),
         f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"))


if __name__ == "__main__":
    build_database(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "regional_health.db"))
    print("演示库已生成。")