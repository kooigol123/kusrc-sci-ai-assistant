# Science Sriracha Assistant 🤖

AI-powered chatbot for the **Faculty of Science, Kasetsart University Sriracha Campus** — automating student inquiries about curricula, registration procedures, and official documents, powered by Google Gemini.

🔗 **Live demo:** [kusrc-science-assistant.onrender.com](https://kusrc-science-assistant.onrender.com/)

## ✨ Features

- **AI Q&A Chatbot** — answers student questions using Gemini (gemini-3.5-flash-lite) with automated multi-key failover and quota-saving intent filters
- **Document Center** — searchable downloads for 20+ official forms and curriculum PDFs for 6 degree programs, featuring live search, instant in-browser PDF preview, and a mobile-responsive layout
- **Authentication & Guest Mode** — secure LINE Login (OAuth 2.0 with CSRF state verification) alongside a guest mode allowing up to 5 trial questions without logging in
- **Admin Dashboard** — real-time visitor statistics, filterable and exportable chat logs, download tracking, and XSS-protected administration
- **Smart Link Fallbacks** — automated action chips providing verified direct links to MyKU registration, the Registrar Office, and TCAS admission systems
- **Automated Evaluation** — `eval.py` measures answer accuracy and appropriateness for continuous prompt and knowledge base benchmarking
- **Data Pipeline** — `scraper.py` collects faculty announcements and calendar dates; curriculum PDFs pre-processed to text for AI retrieval

## 🛠 Tech Stack

| Layer      | Technology                                              |
| ---------- | ------------------------------------------------------- |
| Backend    | Python, Flask, Flask-Login, Flask-SQLAlchemy            |
| AI         | Google Gemini API (gemini-3.5-flash-lite, multi-key)    |
| Auth       | LINE Login (OAuth 2.0), Flask-Login, Secure Session     |
| Database   | SQLite (dev with WAL mode) / PostgreSQL (production)    |
| Frontend   | HTML, CSS, JavaScript (Jinja2 templates, Responsive)   |
| Data       | BeautifulSoup, PyPDF2, openpyxl                         |
| Deployment | Render (gunicorn)                                       |

## 🚀 Getting Started

```bash
# Clone the repository
git clone https://github.com/Gunqeq/science-assistant.git
cd science-assistant

# Create a virtual environment and install dependencies
python -m venv venv
venv\Scripts\activate        # Windows (use source venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

# Configure environment variables
# Create a .env file with:
#   SECRET_KEY=your_secret_key
#   GEMINI_API_KEY=your_primary_gemini_api_key
#   GEMINI_API_KEY2=your_secondary_gemini_api_key
#   GEMINI_API_KEY3=your_tertiary_gemini_api_key
#   GEMINI_MODEL=gemini-3.5-flash-lite
#   LINE_CLIENT_ID=your_line_channel_id
#   LINE_CLIENT_SECRET=your_line_channel_secret
#   LINE_REDIRECT_URI=http://localhost:3000/auth/line/callback
#   ADMIN_USERNAME=admin
#   ADMIN_PASSWORD=your_admin_password

# Run the app
python app.py
```

## 🧪 Evaluation

```bash
python eval.py
```
Runs automated tests against the FAQ benchmark set, scoring answer accuracy and appropriateness.

## 📸 Screenshots

| 💬 User — Chat | 🛠 Admin — Dashboard |
|:---:|:---:|
| <img width="450" alt="User chat interface" src="https://github.com/user-attachments/assets/7be5f293-d689-4b51-86ab-4444bfbdfc62" /> | <img width="450" alt="Admin dashboard" src="https://github.com/user-attachments/assets/1111ad49-4738-4f5d-bdfa-7b1abe5056f6" /> |

## 👥 Authors & Contributors

**Development Team (Computer Science, Faculty of Science, Kasetsart University Sriracha Campus):**
- **Songwut Sudtalai** — [@Gunqeq](https://github.com/Gunqeq)

**Enhanced & Maintained by:**
- **Anupat Sombatsri** — [@kooigol123](https://github.com/kooigol123) (Faculty Student Assistant)
