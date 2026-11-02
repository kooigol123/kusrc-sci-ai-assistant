# Science Sriracha Assistant

ระบบผู้ช่วยอัจฉริยะคณะวิทยาศาสตร์ ศรีราชา มหาวิทยาลัยเกษตรศาสตร์ วิทยาเขตศรีราชา ให้บริการตอบคำถามนิสิตเกี่ยวกับหลักสูตร การลงทะเบียน และเอกสารคำร้อง ผ่าน Google Gemini API

ลิงก์ทดลองใช้งาน: https://kusrc-science-assistant.onrender.com/

---

## 1. ฟีเจอร์หลักของระบบ (Features)

1. ระบบถามตอบอัจฉริยะ ตอบคำถามนิสิตด้วยโมเดล gemini-3.1-flash-lite พร้อมระบบสลับคีย์อัตโนมัติเมื่อคีย์หลักติดโควตา
2. ศูนย์รวมเอกสารและแบบฟอร์ม ค้นหาและดาวน์โหลดเอกสารคำร้องและหลักสูตร 6 สาขาวิชา พร้อมเปิดดูตัวอย่าง PDF ผ่านเบราว์เซอร์
3. ระบบเข้าสู่ระบบและโหมดทดลองใช้งาน รองรับ LINE Login แบบ OAuth 2.0 พร้อมโหมด Guest ทดลองใช้งานได้ 5 คำถามทันที
4. แดชบอร์ดผู้ดูแลระบบ สรุปสถิติผู้เข้าชม บันทึกประวัติการสนทนา ค้นหาและส่งออกไฟล์ CSV พร้อมระบบป้องกันช่องโหว่ XSS
5. ปุ่มทางลัดอัตโนมัติ แนบลิงก์ระบบสำคัญ เช่น MyKU สำนักทะเบียน และระบบรับสมัคร TCAS อัตโนมัติเมื่อคำถามเกี่ยวข้อง
6. ชุดทดสอบวัดคุณภาพคำตอบ สคริปต์ eval.py ทดสอบวัดความถูกต้องและคะแนนความสมบูรณ์ของคำตอบอย่างต่อเนื่อง
7. ระบบดึงข้อมูลอัตโนมัติ สคริปต์ scraper.py ดึงประกาศและปฏิทินการศึกษา พร้อมสกัดข้อความหลักสูตรเข้าสู่ฐานข้อมูล

---

## 2. เครื่องมือและเทคโนโลยี (Tech Stack)

1. Backend: Python, Flask, Flask-Login, Flask-SQLAlchemy
2. AI Model: Google Gemini API (gemini-3.1-flash-lite พร้อมระบบสำรองหลายคีย์)
3. Authentication: LINE Login (OAuth 2.0), Flask-Login, Secure Session Cookie
4. Database: SQLite (พร้อมโหมด WAL สำหรับพัฒนา) และ PostgreSQL (สำหรับ Production)
5. Frontend: HTML, CSS, JavaScript (รองรับทั้งคอมพิวเตอร์และมือถือ)
6. Data Processing: BeautifulSoup4, PyPDF2, openpyxl
7. Deployment: Render (Gunicorn WSGI)

---

## 3. ขั้นตอนการติดตั้งและรันระบบ (Getting Started)

1. โคลนโปรเจกต์และเข้าสู่โฟลเดอร์:
```bash
git clone https://github.com/Gunqeq/science-assistant.git
cd science-assistant
```

2. สร้าง venv และติดตั้ง dependencies:
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

3. สร้างไฟล์ .env กำหนดค่าคอนฟิก:
```text
SECRET_KEY=your_secret_key
GEMINI_API_KEY=your_primary_gemini_api_key
GEMINI_API_KEY2=your_secondary_gemini_api_key
GEMINI_API_KEY3=your_tertiary_gemini_api_key
GEMINI_MODEL=gemini-3.1-flash-lite
LINE_CLIENT_ID=your_line_channel_id
LINE_CLIENT_SECRET=your_line_channel_secret
LINE_REDIRECT_URI=http://localhost:3000/auth/line/callback
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_admin_password
```

4. รันเซิร์ฟเวอร์:
```bash
python app.py
```

---

## 4. การทดสอบวัดผล (Evaluation)

1. รันชุดทดสอบความถูกต้องของคำตอบ:
```bash
python eval.py
```

---

## 5. คณะผู้พัฒนา (Authors)

1. คณะผู้พัฒนาหลัก (นิสิตหลักสูตรวิทยาการคอมพิวเตอร์ คณะวิทยาศาสตร์ ศรีราชา):
- นายทรงวุฒิ สุดตาลัย (Songwut Sudtalai)
- นายภัทราวุธ สุขบท (Pattarawut Sukbot)
- นายอุกฤษฎ์ จรเอ้กา (Ukriss Joraeka)

2. ดูแลและปรับปรุงระบบเพิ่มเติม:
- นายอนุภัทร สมบัติศรี (Anupat Sombatsri)
