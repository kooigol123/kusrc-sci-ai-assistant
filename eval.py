"""
eval.py — ทดสอบความแม่นยำของ Science Assistant AI
ใช้สำหรับรายงานโปรเจคจบ

วิธีรัน:
  python eval.py                      # รันทั้งหมด (เว้นระยะ 4.2 วินาที กันชนเพดาน 15 RPM)
  python eval.py --limit 5            # ทดสอบด่วน 5 ข้อแรก ประหยัดโควตา
  python eval.py --category หลักสูตร   # ทดสอบเฉพาะหมวดหมู่
  python eval.py --ids 1,2,5          # ทดสอบเฉพาะข้อที่ระบุ
  python eval.py --delay 5.0          # ปรับระยะเวลาเว้นช่วงระหว่างข้อ (วินาที)
  python eval.py --model gemini-3.1-flash-lite

ผลลัพธ์:
  - แสดง score %-accuracy และความเร็วเฉลี่ย (latency)
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
    {
        "id": 21,
        "question": "เปิดเทอมวันไหนครับ",
        "keywords": ["เปิดภาค", "มิถุนายน", "ภาคต้น"],
        "category": "ปฏิทินการศึกษา"
    },
    {
        "id": 22,
        "question": "สอบกลางภาควันไหนครับ",
        "keywords": ["สอบกลางภาค", "สิงหาคม", "ภาคต้น"],
        "category": "ปฏิทินการศึกษา"
    },
    {
        "id": 23,
        "question": "สอบปลายภาควันไหนครับ",
        "keywords": ["สอบไล่", "ตุลาคม", "ภาคต้น"],
        "category": "ปฏิทินการศึกษา"
    },
    {
        "id": 24,
        "question": "ปิดเทอมวันไหนครับ",
        "keywords": ["ปิดภาค", "พฤศจิกายน", "ภาคต้น"],
        "category": "ปฏิทินการศึกษา"
    },
    {
        "id": 25,
        "question": "อาจารย์สาขาวิทยาการคอมพิวเตอร์มีใครบ้าง",
        "keywords": ["สุชาดา", "ฉัตรชัย", "จิรวรรณ"],
        "category": "บุคลากร"
    },
    {
        "id": 26,
        "question": "รองคณบดีคณะวิทยาศาสตร์มีใครบ้าง",
        "keywords": ["ณรงค์ฤทธิ์", "วรสิทธิ", "ทองใส"],
        "category": "บุคลากร"
    },
    {
        "id": 27,
        "question": "สวัสดีครับ",
        "keywords": ["Science Assistant", "คณะวิทยาศาสตร์", "ศรีราชา"],
        "category": "ระบบคัดกรอง"
    },
    {
        "id": 28,
        "question": "ใช้โมเดลไร",
        "keywords": ["Science Assistant", "คณะวิทยาศาสตร์", "ศรีราชา"],
        "category": "ระบบคัดกรอง"
    },
    # ── บทบาทที่ 1: นักเรียน ม.6 ที่กำลังจะเข้าศึกษา (Prospective Student) ──
    {
        "id": 29,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[TCAS เกณฑ์รับเข้า]",
        "question": "รอบ Portfolio (TCAS 1) ของคณะวิทย์ วิทยาการคอมพิวเตอร์ รับเกรดเฉลี่ยขั้นต่ำเท่าไหร่ ต้องใส่ผลงานอะไรในพอร์ตบ้าง",
        "keywords": ["portfolio", "gpax", "วิทยาการคอมพิวเตอร์", "เกณฑ์"],
        "category": "การรับเข้าศึกษา (ม.6)"
    },
    {
        "id": 30,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[เทียบหลักสูตร]",
        "question": "วิทยาการคอมพิวเตอร์ที่วิทยาเขตศรีราชา เรียนต่างจากบางเขนยังไงบ้าง เน้นอะไรเป็นพิเศษ",
        "keywords": ["วิทยาการคอมพิวเตอร์", "ศรีราชา", "หลักสูตร", "ซอฟต์แวร์"],
        "category": "หลักสูตร (ม.6)"
    },
    {
        "id": 31,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[เงื่อนไขแผนการเรียน]",
        "question": "จบ ม.6 ศิลป์-คำนวณ อยากเข้าคณะวิทยาศาสตร์ มีสาขาไหนที่เปิดรับบ้างไหม",
        "keywords": ["ศิลป์-คำนวณ", "วิทยาการคอมพิวเตอร์", "ดิจิทัล", "คณิตศาสตร์"],
        "category": "การรับเข้าศึกษา (ม.6)"
    },
    {
        "id": 32,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[ค่าใช้จ่ายแรกเข้า]",
        "question": "ค่าเทอมสาขาไอทีกับวิทยาการคอมเทอมละเท่าไหร่ เป็นแบบเหมาจ่ายไหม มีค่าแรกเข้าอะไรอีกบ้าง",
        "keywords": ["เหมาจ่าย", "บาท", "ภาคการศึกษา", "ค่าธรรมเนียม"],
        "category": "ค่าใช้จ่าย (ม.6)"
    },
    {
        "id": 33,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[หอพักเด็กปี 1]",
        "question": "เด็กปี 1 คณะวิทย์ บังคับอยู่หอในไหมคะ แล้วหอในมีแบบไหนบ้าง ค่าหอประมาณกี่บาท",
        "keywords": ["หอใน", "บังคับ", "พัดลม", "แอร์"],
        "category": "ชีวิตความเป็นอยู่ (ม.6)"
    },
    {
        "id": 34,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[การสอบสัมภาษณ์]",
        "question": "สัมภาษณ์รอบพอร์ตคณะวิทย์ยากไหม อาจารย์ถามเป็นภาษาอังกฤษหรือเปล่า",
        "keywords": ["สัมภาษณ์", "portfolio", "พอร์ต", "คำถาม"],
        "category": "การรับเข้าศึกษา (ม.6)"
    },
    {
        "id": 35,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[ทุนการศึกษาแรกเข้า]",
        "question": "มีทุนการศึกษาสำหรับเด็ก ม.6 ที่เรียนดีหรือขาดแคลนทุนทรัพย์ตั้งแต่เทอมแรกเลยไหม",
        "keywords": ["ทุน", "เรียนดี", "ขาดแคลน", "กยศ"],
        "category": "ทุนการศึกษา (ม.6)"
    },
    {
        "id": 36,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[ชีวิตความเป็นอยู่]",
        "question": "อยู่ มก.ศรีราชา จำเป็นต้องมีมอเตอร์ไซค์ไหม การเดินทางในมอมีรถตะลัยรับส่งไหม",
        "keywords": ["รถตะลัย", "เดินทาง", "ศรีราชา", "วิทยาเขต"],
        "category": "ชีวิตความเป็นอยู่ (ม.6)"
    },
    {
        "id": 37,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[Edge Case - ข้ามคณะ]",
        "question": "ถ้าอยากยื่นเข้าวิศวะคอม หรือโลจิสติกส์ ต้องยื่นรอบไหน ใช้เกรดเท่าไหร่",
        "keywords": ["วิศวกรรม", "คณะ", "admissions.src.ku.ac.th", "วิทยาศาสตร์"],
        "category": "ขอบเขตระบบ (Edge Case)"
    },
    {
        "id": 38,
        "role": "นักเรียน ม.6 (Prospective Student)",
        "tag": "[Out of Scope - ชวนคุยเล่น]",
        "question": "พี่บอทคะ มีร้านหมูกระทะหรือคาเฟ่เด็ดๆ แถวหลังมอศรีราชาแนะนำไหม",
        "keywords": ["คณะวิทยาศาสตร์", "หลักสูตร", "การศึกษา", "ข้อมูล"],
        "category": "ขอบเขตระบบ (Out of Scope)"
    },

    # ── บทบาทที่ 2: ผู้ปกครอง (Parent) ──
    {
        "id": 39,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[การผ่อนชำระ/กู้ยืม]",
        "question": "ค่าเทอมสามารถแบ่งจ่ายเป็นงวดๆ ได้ไหม หรือถ้าจะกู้ กยศ. ต้องเตรียมเอกสารอะไรให้ทางคณะบ้าง",
        "keywords": ["ผ่อนผัน", "กยศ", "ค่าธรรมเนียม", "แบบฟอร์ม"],
        "category": "การเงินและทุน (ผู้ปกครอง)"
    },
    {
        "id": 40,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[ความปลอดภัยของหอพัก]",
        "question": "หอพักในมหาวิทยาลัยมีระบบดูแลความปลอดภัยยังไง มีคนคุมหอและมีเวลาปิด-เปิดหอไหม",
        "keywords": ["หอพัก", "ความปลอดภัย", "เวลา", "นิสิต"],
        "category": "หอพักและความเป็นอยู่ (ผู้ปกครอง)"
    },
    {
        "id": 41,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[แนวทางอาชีพ/ความมั่นคง]",
        "question": "ถ้าลูกเรียนจบวิทยาศาสตร์ เทคโนโลยีสารสนเทศ ที่ศรีราชา ตลาดงานแถวชลบุรีและ EEC รองรับเยอะไหม จบมาทำงานอะไรได้บ้าง",
        "keywords": ["งาน", "เทคโนโลยีสารสนเทศ", "นิคม", "โปรแกรมเมอร์"],
        "category": "แนวทางอาชีพ (ผู้ปกครอง)"
    },
    {
        "id": 42,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[การติดตามผลการเรียน]",
        "question": "ผู้ปกครองสามารถเข้าไปดูผลการเรียน เกรดเฉลี่ย หรือเช็คการเข้าชั้นเรียนของลูกได้ทางไหนบ้าง",
        "keywords": ["myku", "ผลการเรียน", "เกรด", "นิสิต"],
        "category": "การศึกษา (ผู้ปกครอง)"
    },
    {
        "id": 43,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[การดูแลนิสิตกลุ่มเสี่ยง]",
        "question": "ถ้าลูกมีปัญหาเรื่องการปรับตัวหรือเกรดตกจนเสี่ยงติดโปร ทางคณะมีอาจารย์ที่ปรึกษาช่วยดูแลไหม",
        "keywords": ["อาจารย์ที่ปรึกษา", "เกรด", "ฝ่ายการศึกษา", "ดูแล"],
        "category": "การดูแลนิสิต (ผู้ปกครอง)"
    },
    {
        "id": 44,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[ช่องทางติดต่อเร่งด่วน]",
        "question": "ขอเบอร์โทรศัพท์หรือช่องทางติดต่อฝ่ายการศึกษา คณะวิทยาศาสตร์ ศรีราชา โดยตรงหน่อยค่ะ",
        "keywords": ["038", "ฝ่ายการศึกษา", "อาคาร", "ติดต่อ"],
        "category": "ติดต่อสอบถาม (ผู้ปกครอง)"
    },
    {
        "id": 45,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[การรักษาพยาบาล]",
        "question": "ถ้าลูกป่วยฉุกเฉินตอนดึก ที่มหาลัยมีห้องพยาบาลไหม หรือส่งตัวไปโรงพยาบาลไหนที่ใกล้ที่สุด",
        "keywords": ["พยาบาล", "โรงพยาบาล", "แหลมฉบัง", "สมเด็จ"],
        "category": "สวัสดิภาพนิสิต (ผู้ปกครอง)"
    },
    {
        "id": 46,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[การผ่อนผันทหาร]",
        "question": "เรื่องการผ่อนผันการเกณฑ์ทหารของลูกชาย ทางมหาลัยเริ่มทำเรื่องช่วงเดือนไหน ต้องใช้เอกสารอะไรบ้าง",
        "keywords": ["ผ่อนผัน", "ทหาร", "กองกิจการนิสิต", "เอกสาร"],
        "category": "ระเบียบการ (ผู้ปกครอง)"
    },
    {
        "id": 47,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[Edge Case - รับปริญญา]",
        "question": "กำหนดการพิธีพระราชทานปริญญาบัตรของปีนี้จัดวันไหน และผู้ปกครองต้องจอดรถที่จุดไหน",
        "keywords": ["ปริญญาบัตร", "มหาวิทยาลัย", "กำหนดการ", "ประกาศ"],
        "category": "ขอบเขตระบบ (Edge Case)"
    },
    {
        "id": 48,
        "role": "ผู้ปกครอง (Parent)",
        "tag": "[Out of Scope - นโยบายมหาวิทยาลัย]",
        "question": "ทำไมค่าบำรุงมหาวิทยาลัยถึงปรับขึ้น เอาเงินส่วนนี้ไปพัฒนาอะไรบ้าง",
        "keywords": ["มหาวิทยาลัย", "คณะวิทยาศาสตร์", "ระเบียบ", "ข้อมูล"],
        "category": "ขอบเขตระบบ (Out of Scope)"
    },

    # ── บทบาทที่ 3: นิสิตปัจจุบัน (Current Student) ──
    {
        "id": 49,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[คำร้องขาดสอบ]",
        "question": "ถ้าป่วยกะทันหันวันสอบปลายภาค ต้องยื่นเรื่องขอสอบชดเชยยังไง ภายในกี่วัน ใช้ใบรับรองแพทย์คลินิกได้ไหม",
        "keywords": ["สอบชดเชย", "ใบรับรองแพทย์", "7 วัน", "คำร้อง"],
        "category": "เอกสารและคำร้อง (นิสิต)"
    },
    {
        "id": 50,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[การดรอปเรียน]",
        "question": "หมดเขตถอนรายวิชา (W) วันไหน แล้วถ้าถอนไปจนหน่วยกิตเหลือต่ำกว่า 9 หน่วยกิตได้ไหม",
        "keywords": ["ถอน", "w", "หน่วยกิต", "9 หน่วยกิต"],
        "category": "การลงทะเบียน (นิสิต)"
    },
    {
        "id": 51,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[ตารางสอบ/ห้องสอบ]",
        "question": "ตารางสอบปลายภาคเทอมนี้ออกหรือยัง ดูห้องสอบกับเวลาสอบได้ที่ไหน",
        "keywords": ["ตารางสอบ", "ห้องสอบ", "regis.src.ku.ac.th", "สำนักทะเบียน"],
        "category": "การสอบ (นิสิต)"
    },
    {
        "id": 52,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[ปัญหาการลงทะเบียน]",
        "question": "วิชาบังคับเซคเต็ม ขอเปิดเซคเพิ่ม (Request Section) ต้องทำเรื่องยังไง เอกสารต้องให้อาจารย์ท่านไหนเซ็นบ้าง",
        "keywords": ["เปิดหมู่เรียน", "คำร้อง", "อาจารย์ผู้สอน", "ภาควิชา"],
        "category": "การลงทะเบียน (นิสิต)"
    },
    {
        "id": 53,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[ลงทะเบียนเกินหน่วยกิต]",
        "question": "เทอมสุดท้ายอยากจบ อยากลงทะเบียนเกิน 22 หน่วยกิต (Overload) ต้องยื่นคำร้องอะไร",
        "keywords": ["ลงทะเบียนเกิน", "หน่วยกิต", "คำร้อง", "22"],
        "category": "การลงทะเบียน (นิสิต)"
    },
    {
        "id": 54,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[การใช้สิ่งอำนวยความสะดวก]",
        "question": "ห้องแล็บคอมพิวเตอร์ที่ตึก 23 เปิดให้นิสิตเข้าไปใช้งานได้ถึงกี่โมง เสาร์-อาทิตย์ เข้าได้ไหม",
        "keywords": ["ห้องปฏิบัติการ", "ตึก", "แล็บ", "คอมพิวเตอร์"],
        "category": "สิ่งอำนวยความสะดวก (นิสิต)"
    },
    {
        "id": 55,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[การแก้ F / ลงเรียนซ้ำ]",
        "question": "ติด F วิชาแคลคูลัส สามารถลงเรียนใหม่ในเทอม Summer ได้ไหม หรือต้องรอเปิดเทอมใหญ่",
        "keywords": ["ลงเรียนใหม่", "f", "ภาคฤดูร้อน", "เปิดสอน"],
        "category": "ระเบียบการเรียน (นิสิต)"
    },
    {
        "id": 56,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[เอกสารสูญหาย]",
        "question": "ทำบัตรนิสิตหาย ต้องไปแจ้งความก่อนไหม หรือติดต่อทำบัตรใหม่ที่สำนักทะเบียนได้เลย",
        "keywords": ["บัตรนิสิต", "สำนักทะเบียน", "ทำบัตร", "ใบแทน"],
        "category": "เอกสารและคำร้อง (นิสิต)"
    },
    {
        "id": 57,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[Edge Case - ปัญหาระบบไอที]",
        "question": "รหัส Non-KUNet ลืมรหัสผ่านหรือบัญชีโดนล็อค แจ้งรีเซ็ตรหัสผ่านได้ที่สำนักไหน",
        "keywords": ["นนทรี", "สำนักบริการคอมพิวเตอร์", "accounts.ku.ac.th", "รหัสผ่าน"],
        "category": "ขอบเขตระบบ (Edge Case)"
    },
    {
        "id": 58,
        "role": "นิสิตปัจจุบัน (Current Student)",
        "tag": "[Out of Scope - ขอให้ทำการบ้าน]",
        "question": "ช่วยเขียนโค้ดภาษา C คำนวณเกรดตัดเกรด A-F ส่งอาจารย์หน่อยครับ",
        "keywords": ["คณะวิทยาศาสตร์", "ผู้ช่วย", "ข้อมูล", "หลักสูตร"],
        "category": "ขอบเขตระบบ (Out of Scope)"
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


def ask_with_retry(ask_fn, question: str, max_retries: int = 3):
    """เรียกถามพร้อมระบบดักจับ Rate Limit 429 และรอระบบคลายตัวอัตโนมัติ"""
    backoffs = [15, 30, 45]
    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            resp, files, links = ask_fn(question)
            lat = round(time.time() - t0, 2)
            return resp, files, links, lat, None
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str
            if is_rate_limit and attempt < max_retries:
                wait_sec = backoffs[min(attempt, len(backoffs) - 1)]
                print(f"     ⚠️  [Rate Limit] ติดโควตา 15 RPM พักรอ {wait_sec} วินาทีก่อนลองใหม่ (ครั้งที่ {attempt + 1}/{max_retries})...")
                time.sleep(wait_sec)
                continue
            return "", [], [], 0.0, str(e)


def run_evaluation(args=None):
    import argparse
    parser = argparse.ArgumentParser(description="Science Assistant — AI Accuracy Evaluation")
    parser.add_argument("--limit", type=int, default=0, help="จำกัดจำนวนข้อที่จะทดสอบ (0 = ทั้งหมด)")
    parser.add_argument("--category", type=str, default="", help="กรองเฉพาะหมวดหมู่ที่ต้องการ (เช่น หลักสูตร, เอกสาร)")
    parser.add_argument("--ids", type=str, default="", help="ระบุรหัสข้อที่ต้องการทดสอบ คั่นด้วยจุลภาค (เช่น 1,2,5)")
    parser.add_argument("--delay", type=float, default=4.2, help="ระยะเวลาพักระหว่างข้อ (วินาที) เพื่อป้องกัน 15 RPM (ค่าเริ่มต้น: 4.2)")
    parser.add_argument("--model", type=str, default="", help="เลือกโมเดล Gemini ที่จะทดสอบ (เช่น gemini-3.1-flash-lite)")

    parsed_args = parser.parse_args(args)

    if parsed_args.model:
        os.environ["GEMINI_MODEL"] = parsed_args.model

    # Import app context
    from app import app, ask_gemini, CHAT_CONTEXT

    # กรองชุดทดสอบตามเงื่อนไข CLI
    cases = list(TEST_CASES)
    if parsed_args.category:
        cases = [c for c in cases if parsed_args.category.lower() in c.get("category", "").lower()]
    if parsed_args.ids:
        target_ids = set(int(x.strip()) for x in parsed_args.ids.split(",") if x.strip().isdigit())
        cases = [c for c in cases if c.get("id") in target_ids]
    if parsed_args.limit > 0:
        cases = cases[:parsed_args.limit]

    active_model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    print("=" * 60)
    print("  Science Assistant — AI Accuracy Evaluation")
    print(f"  Target Model:   {active_model}")
    print(f"  Knowledge base: {len(CHAT_CONTEXT)} documents loaded")
    print(f"  Total Cases:    {len(cases)} questions")
    print(f"  Delay per case: {parsed_args.delay}s (กัน Rate Limit 15 RPM)")
    print("=" * 60)

    if not cases:
        print("  ไม่พบชุดคำถามตามเงื่อนไขที่ระบุ")
        return {}

    results = []
    passed_count = 0
    total_latency = 0.0

    for idx, tc in enumerate(cases, 1):
        print(f"\n[{idx:02d}/{len(cases)}] (ID: {tc['id']:02d}) {tc['question']}")
        print(f"     Category: {tc['category']}")

        response, files, links, latency, error = ask_with_retry(ask_gemini, tc["question"])

        if error:
            print(f"     ⚠️  Error: {error}")
            results.append({
                "id": tc["id"],
                "role": tc.get("role", "ชุดคำถามทั่วไป (General)"),
                "question": tc["question"],
                "category": tc["category"],
                "passed": False,
                "error": error,
                "latency_sec": latency
            })
        else:
            passed, found, missing = score_response(response, tc["keywords"])
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"     {status} ({latency}s) | Found: {found} | Missing: {missing}")
            print(f"     Response: {response[:120]}...")

            if passed:
                passed_count += 1
            total_latency += latency

            results.append({
                "id":        tc["id"],
                "role":      tc.get("role", "ชุดคำถามทั่วไป (General)"),
                "tag":       tc.get("tag", ""),
                "question":  tc["question"],
                "category":  tc["category"],
                "keywords":  tc["keywords"],
                "response":  response,
                "passed":    passed,
                "found_keywords":   found,
                "missing_keywords": missing,
                "suggested_files":  files,
                "suggested_links":  links,
                "latency_sec":      latency,
            })

        # หน่วงเวลาป้องกันชนเพดาน 15 RPM (ยกเว้นข้อสุดท้าย)
        if idx < len(cases):
            time.sleep(parsed_args.delay)

    # Summary
    total_tested = len(cases)
    accuracy = (passed_count / total_tested * 100) if total_tested else 0
    valid_latencies = [r.get("latency_sec", 0) for r in results if r.get("latency_sec")]
    avg_latency = round(sum(valid_latencies) / len(valid_latencies), 2) if valid_latencies else 0.0

    print("\n" + "=" * 60)
    print(f"  RESULT: {passed_count}/{total_tested} passed  ({accuracy:.1f}% accuracy)")
    print(f"  Model Speed: Average {avg_latency}s per response (Total: {round(total_latency, 1)}s)")

    # Breakdown by Category & Role
    from collections import defaultdict
    by_cat = defaultdict(lambda: {"pass": 0, "total": 0})
    by_role = defaultdict(lambda: {"pass": 0, "total": 0})
    for r in results:
        by_cat[r["category"]]["total"] += 1
        if r.get("passed"):
            by_cat[r["category"]]["pass"] += 1

        role = r.get("role") or "ชุดคำถามทั่วไป (General)"
        by_role[role]["total"] += 1
        if r.get("passed"):
            by_role[role]["pass"] += 1

    print("\n  Role / Persona Breakdown:")
    for role, s in sorted(by_role.items()):
        pct = s["pass"] / s["total"] * 100
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        print(f"    {role:<35} {bar}  {s['pass']}/{s['total']} ({pct:.0f}%)")

    print("\n  Category Breakdown:")
    for cat, s in sorted(by_cat.items()):
        pct = s["pass"] / s["total"] * 100
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        print(f"    {cat:<35} {bar}  {s['pass']}/{s['total']} ({pct:.0f}%)")

    print("=" * 60)

    # บันทึก JSON สำหรับใส่รายงาน
    output = {
        "eval_date":    __import__('datetime').datetime.now().isoformat(),
        "model":        active_model,
        "total":        total_tested,
        "passed":       passed_count,
        "accuracy_pct": round(accuracy, 2),
        "avg_latency":  avg_latency,
        "by_role":      dict(by_role),
        "by_category":  dict(by_cat),
        "results":      results,
    }
    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  บันทึกผลลัพธ์ที่: eval_results.json")
    return output


if __name__ == "__main__":
    run_evaluation()