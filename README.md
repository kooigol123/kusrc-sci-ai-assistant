# Science Sriracha Assistant 🤖

AI-powered chatbot for the **Faculty of Science, Kasetsart University Sriracha Campus** — automating student inquiries about curricula, registration procedures, and official documents, powered by Google Gemini.

🔗 **Live demo:** ([https://kusrc-science-assistant.onrender.com/](https://kusrc-sci-ai-assistant.onrender.com/))

## ✨ Features

- **AI Q&A Chatbot** — answers student questions using Gemini 1.5 Flash with a curated FAQ knowledge base and curriculum documents
- **Document Center** — searchable downloads for 20+ official forms (registration, leave, refunds, transfers) and curriculum PDFs for 6 degree programs
- **User System** — registration and login with Flask-Login, session security (HttpOnly, SameSite cookies)
- **Admin Dashboard** — monitor chat logs, download statistics, and manage content
- **Automated Evaluation** — `eval.py` measures answer accuracy and appropriateness for iterative prompt improvement
- **Data Pipeline** — `scraper.py` collects faculty information; curriculum PDFs pre-processed to text for AI retrieval

## 🛠 Tech Stack

| Layer      | Technology                                  |
| ---------- | ------------------------------------------- |
| Backend    | Python, Flask, Flask-Login, Flask-SQLAlchemy |
| AI         | Google Gemini API (gemini-1.5-flash)        |
| Database   | SQLite (dev) / PostgreSQL (production)      |
| Frontend   | HTML, CSS, JavaScript (Jinja2 templates)    |
| Data       | BeautifulSoup, PyPDF2, openpyxl             |
| Deployment | Render (gunicorn)                           |

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
#   GEMINI_API_KEY=your_api_key
#   SECRET_KEY=your_secret_key

# Run the app
python app.py
```

## 🧪 Evaluation

```bash
python eval.py
```
Runs automated tests against the FAQ set, scoring answer accuracy and appropriateness.

## 📸 Screenshots

| 💬 User — Chat | 🛠 Admin — Dashboard |
|:---:|:---:|
| <img width="450" alt="User chat interface" src="https://github.com/user-attachments/assets/7be5f293-d689-4b51-86ab-4444bfbdfc62" /> | <img width="450" alt="Admin dashboard" src="https://github.com/user-attachments/assets/1111ad49-4738-4f5d-bdfa-7b1abe5056f6" /> |


## 👤 Author

**Songwut Sudtalai** — [github.com/Gunqeq](https://github.com/Gunqeq)
