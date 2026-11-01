# คู่มือการดูแลและพัฒนาระบบ Science Assistant

---

## 1. คำสั่งสำหรับรันและทดสอบระบบ (Terminal Commands)

1. สร้างและเปิดใช้งาน venv บน Windows:
```bash
python -m venv venv
venv\Scripts\activate
```
2. ติดตั้ง pip ที่ต้องใช้:
```bash
pip install -r requirements.txt
```
3. รันเซิร์ฟเวอร์ Local:
```bash
python app.py
```
4. ทดสอบรัน Scraper ดึงข้อมูลเว็บและปฏิทิน:
```bash
python scraper.py
```
5. รันชุดทดสอบวัดคุณภาพคำตอบบอท (Eval):
```bash
python eval.py
```

---

## 2. ตัวแปรคอนฟิกและตัวอย่างไฟล์ .env (Environment Variables)

1. SECRET_KEY คือ คีย์สำหรับเข้ารหัส Session ของ Flask
2. GEMINI_API_KEY คือ คีย์หลักสำหรับเชื่อมต่อ Google Gemini API จาก aistudio.google.com
3. GEMINI_API_KEY2 และ GEMINI_API_KEY3 คือ คีย์สำรองสลับอัตโนมัติเมื่อคีย์หลักโควตาเต็มหรือติด Rate Limit
4. LINE_CLIENT_ID และ LINE_CLIENT_SECRET คือ ข้อมูล Channel จาก LINE Developers สำหรับระบบ LINE Login
5. LINE_REDIRECT_URI คือ URL ปลายทางสำหรับรับ Callback ของ LINE Login
6. ADMIN_USERNAME และ ADMIN_PASSWORD คือ บัญชีล็อกอินเข้าหน้า Admin Dashboard
7. ตัวอย่างการกำหนดค่าในไฟล์ .env สำหรับคัดลอกไปใช้งาน:
```text
SECRET_KEY=your_secret_hex_key_here
GEMINI_API_KEY=AIzaSyD_ExampleKey1_xxxxxxxxxxxxxxxx
GEMINI_API_KEY2=AIzaSyD_ExampleKey2_xxxxxxxxxxxxxxxx
GEMINI_API_KEY3=AIzaSyD_ExampleKey3_xxxxxxxxxxxxxxxx
LINE_CLIENT_ID=2001234567
LINE_CLIENT_SECRET=abcdef1234567890abcdef1234567890
LINE_REDIRECT_URI=http://127.0.0.1:5000/auth/line/callback
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_strong_password
```

---

## 3. โครงสร้างไฟล์หลักของระบบ (Core Project Files)

1. app.py คือ เซิร์ฟเวอร์หลัก จัดการ Routing, API, ฐานข้อมูล และการเชื่อมต่อ Gemini
2. prompt.py คือ System Prompt และกฎพฤติกรรมการตอบของ AI
3. scraper.py คือ สคริปต์ดึงข้อมูลหน้าเว็บคณะและปฏิทินการศึกษา
4. eval.py คือ สคริปต์ชุดทดสอบและประเมินคุณภาพคำตอบของแชทบอท
5. templates/ คือ โฟลเดอร์หน้าเว็บหลัก ประกอบด้วย chat.html, Dashboard.html และ login.html
6. static/pdfs/ คือ โฟลเดอร์เก็บเอกสารคำร้องและแบบฟอร์ม PDF สำหรับแนะนำดาวน์โหลด
7. static/curriculum_text/ คือ โฟลเดอร์เก็บเนื้อหาหลักสูตรฉบับเต็มสำหรับสืบค้นบริบท

---

## 4. URL สำหรับเข้าใช้งานระบบ (System Endpoints)

1. หน้าแชทบอทหลัก: http://127.0.0.1:5000/
2. หน้าล็อกอินนิสิต: http://127.0.0.1:5000/login
3. ทางลัดเข้าโหมดทดลองใช้งาน: http://127.0.0.1:5000/auth/guest
4. หน้าล็อกอินแอดมิน: http://127.0.0.1:5000/admin/login
5. หน้าแดชบอร์ดแอดมิน: http://127.0.0.1:5000/admin

---

## 5. การดีพลอยบน Render และการป้องกันเซิร์ฟเวอร์หลับ (Deployment & Keep-Alive)

1. Start Command ของ Gunicorn บน Render จำกัด 1 Worker และ 4 Threads เพื่อคุมแรมไม่ให้เกิน 512 MB:
```bash
gunicorn -w 1 --threads 4 app:app
```
2. Render จะปิดเว็บเครื่องอัตโนมัติหากไม่มีทราฟฟิกเข้ามาเกิน 15 นาที
3. ป้องกันเครื่องหลับโดยตั้งค่า UptimeRobot ให้ยิง GET ไปที่ URL หน้าเว็บทุก 5 ถึง 10 นาที