"""
eval.py — ทดสอบความแม่นยำของ Science Assistant AI
ใช้สำหรับรายงานโปรเจคจบ

วิธีรัน:
  python eval.py

ผลลัพธ์:
  - แสดง score %-accuracy
  - บันทึก eval_results.json สำหรับใส่รายงาน
"""

import json, re, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

# ── ชุดคำถามทดสอบ (แก้ไข/เพิ่มได้) ─────────────────────────
TEST_CASES = [
    {
        "id": 1,
        "question": "คณะวิทยาศาสตร์ ม.เกษตร ศรีราชา มีสาขาอะไรบ้าง",
        "keywords": ["วิทยาการคอมพิวเตอร์", "เทคโนโลยีสารสนเทศ", "ดิจิทัล"],
        "category": "หลักสูตร"
    },
    {
        "id": 2,
        "question": "ป่วยตอนสอบต้องทำยังไง",
        "keywords": ["ใบลา", "ขอสอบชดเชย", "แพทย์", "7 วัน"],
        "category": "เอกสาร"
    },
    {
        "id": 3,
        "question": "ค่าเทอมสาขาวิทยาการคอมพิวเตอร์เท่าไหร่",
        "keywords": ["บาท", "เทอม", "ค่า"],
        "category": "การเงิน"
    },
    {
        "id": 4,
        "question": "จะลาพักการศึกษาต้องทำยังไง",
        "keywords": ["ลาพัก", "แบบฟอร์ม", "คณบดี"],
        "category": "เอกสาร"
    },
    {
        "id": 5,
        "question": "สมัครเรียน TCAS รอบไหนบ้าง",
        "keywords": ["TCAS", "รอบ", "Portfolio", "Quota"],
        "category": "การสมัคร"
    },
    {
        "id": 6,
        "question": "ขอคืนสภาพนิสิตต้องทำยังไง",
        "keywords": ["คืนสภาพ", "แบบฟอร์ม", "ยื่น"],
        "category": "เอกสาร"
    },
    {
        "id": 7,
        "question": "หลักสูตรพิเศษต่างจากหลักสูตรปกติยังไง",
        "keywords": ["พิเศษ", "ปกติ", "ค่าเทอม"],
        "category": "หลักสูตร"
    },
    {
        "id": 8,
        "question": "ขอผ่อนชำระค่าเทอมได้ไหม",
        "keywords": ["ผ่อน", "ชำระ", "แบบฟอร์ม"],
        "category": "การเงิน"
    },
    {
        "id": 9,
        "question": "ติดต่อฝ่ายการศึกษาได้ที่ไหน",
        "keywords": ["อาคาร", "ฝ่ายการศึกษา", "ชั้น"],
        "category": "ติดต่อ"
    },
    {
        "id": 10,
        "question": "ขอใบรับรองรายวิชาต้องทำยังไง",
        "keywords": ["ใบรับรอง", "รายวิชา", "แบบฟอร์ม"],
        "category": "เอกสาร"
    },
    {
        "id": 11,
        "question": "ถอนวิชาได้ถึงเมื่อไหร่",
        "keywords": ["ถอน", "วิชา", "สัปดาห์"],
        "category": "การลงทะเบียน"
    },
    {
        "id": 12,
        "question": "หลักสูตรวิทยาการคอมพิวเตอร์เรียนกี่ปี",
        "keywords": ["4 ปี", "ปี", "หน่วยกิต"],
        "category": "หลักสูตร"
    },
    {
        "id": 13,
        "question": "สมัครเรียนสายอาชีพได้ไหม",
        "keywords": ["อาชีพ", "สมัคร", "คุณสมบัติ"],
        "category": "การสมัคร"
    },
    {
        "id": 14,
        "question": "อยากย้ายสาขาต้องทำยังไง",
        "keywords": ["ย้ายสาขา", "แบบฟอร์ม", "เปลี่ยน"],
        "category": "เอกสาร"
    },
    {
        "id": 15,
        "question": "ทุนการศึกษามีอะไรบ้าง",
        "keywords": ["ทุน", "การศึกษา", "เงื่อนไข"],
        "category": "การเงิน"
    },
    {
        "id": 16,
        "question": "คณบดีคณะวิทยาศาสตร์คือใคร",
        "keywords": ["คณบดี", "ดร.", "รศ.", "ผศ."],
        "category": "บุคลากร"
    },
    {
        "id": 17,
        "question": "เข้าระบบ MyKU ยังไง",
        "keywords": ["MyKU", "ระบบ", "my.ku.th"],
        "category": "ระบบ"
    },
    {
        "id": 18,
        "question": "ขอ transcript ต้องทำยังไง",
        "keywords": ["transcript", "ใบแสดงผล", "ยื่น"],
        "category": "เอกสาร"
    },
    {
        "id": 19,
        "question": "ลงทะเบียนเพิ่มวิชาต้องทำยังไง",
        "keywords": ["ลงทะเบียน", "เพิ่ม", "แบบฟอร์ม"],
        "category": "การลงทะเบียน"
    },
    {
        "id": 20,
        "question": "สาขาดิจิทัลไซน์เรียนเกี่ยวกับอะไร",
        "keywords": ["ดิจิทัล", "เทคโนโลยี", "เรียน"],
        "category": "หลักสูตร"
    },
]

def score_response(response_text: str, keywords: list) -> tuple[bool, list, list]:
    """ตรวจว่า response มี keyword ที่ต้องการครบไหม"""
    text_lower = response_text.lower()
    found = [kw for kw in keywords if kw.lower() in text_lower]
    missing = [kw for kw in keywords if kw.lower() not in text_lower]
    # pass ถ้าพบ >= 50% ของ keywords
    passed = len(found) / len(keywords) >= 0.5
    return passed, found, missing

def run_evaluation():
    print("=" * 60)
    print("  Science Assistant — AI Accuracy Evaluation")
    print("=" * 60)

    # Import app context
    from app import app, ask_gemini, CHAT_CONTEXT
    print(f"  Knowledge base: {len(CHAT_CONTEXT)} documents loaded")
    print("=" * 60)

    results = []
    passed_count = 0

    for tc in TEST_CASES:
        print(f"\n[{tc['id']:02d}/{len(TEST_CASES)}] {tc['question']}")
        print(f"     Category: {tc['category']}")

        try:
            response, files, links = ask_gemini(tc["question"])
            passed, found, missing = score_response(response, tc["keywords"])

            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"     {status} | Found: {found} | Missing: {missing}")
            print(f"     Response: {response[:120]}...")

            if passed:
                passed_count += 1

            results.append({
                "id":        tc["id"],
                "question":  tc["question"],
                "category":  tc["category"],
                "keywords":  tc["keywords"],
                "response":  response,
                "passed":    passed,
                "found_keywords":   found,
                "missing_keywords": missing,
                "suggested_files":  files,
            })
        except Exception as e:
            print(f"     ⚠️  Error: {e}")
            results.append({
                "id": tc["id"], "question": tc["question"],
                "category": tc["category"], "passed": False,
                "error": str(e)
            })

        time.sleep(1.5)  # กัน rate limit

    # Summary
    accuracy = passed_count / len(TEST_CASES) * 100
    print("\n" + "=" * 60)
    print(f"  RESULT: {passed_count}/{len(TEST_CASES)} passed  ({accuracy:.1f}% accuracy)")

    # Category breakdown
    from collections import defaultdict
    by_cat = defaultdict(lambda: {"pass": 0, "total": 0})
    for r in results:
        by_cat[r["category"]]["total"] += 1
        if r.get("passed"):
            by_cat[r["category"]]["pass"] += 1
    print("\n  Category Breakdown:")
    for cat, s in sorted(by_cat.items()):
        pct = s["pass"] / s["total"] * 100
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        print(f"    {cat:<20} {bar}  {s['pass']}/{s['total']} ({pct:.0f}%)")

    print("=" * 60)

    # บันทึก JSON สำหรับใส่รายงาน
    output = {
        "eval_date":    __import__('datetime').datetime.now().isoformat(),
        "total":        len(TEST_CASES),
        "passed":       passed_count,
        "accuracy_pct": round(accuracy, 2),
        "by_category":  dict(by_cat),
        "results":      results,
    }
    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  บันทึกผลลัพธ์ที่: eval_results.json")
    return output

if __name__ == "__main__":
    run_evaluation()