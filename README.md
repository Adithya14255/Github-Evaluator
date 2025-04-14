# GitHub Resume Analyzer - Setup Instructions

## Requirements

Create a file named `requirements.txt` with the following content:

```
flask==2.3.3
requests==2.31.0
python-dotenv==1.0.0
google-generativeai==0.7.1
PyMuPDF==1.23.7  # For PDF extraction
Werkzeug==2.3.7
```

## Project Structure

```
github-resume-analyzer/
├── .env                  # Environment variables (create this)
├── app.py               # Main application file
├── requirements.txt     # Dependencies
├── uploads/             # Directory for uploaded resumes (created automatically)
└── templates/           # HTML templates
    ├── index.html
    ├── manual_github.html
    ├── result.html
    └── repo_analysis.html
```

## Environment Variables

Create a file named `.env` in the project root with the following content:

```
# GitHub Personal Access Token (Optional but recommended to avoid rate limits)
# Create one at: https://github.com/settings/tokens
GITHUB_TOKEN=your_github_token_here

# Google Gemini API Key
# Get one at: https://makersuite.google.com/app/apikey
GEMINI_API_KEY=your_gemini_api_key_here
```

## Installation Instructions

1. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application**:
   ```bash
   python app.py
   ```

4. Open your browser and navigate to `http://127.0.0.1:5000/`

## Features

1. **Resume Upload & Analysis**
   - Supports PDF and text files
   - Automatically extracts GitHub profile URL
   - Extracts skills mentioned in the resume

2. **GitHub Profile Analysis**
   - Verifies skills claimed in resume
   - Analyzes public repositories
   - Examines coding activity and contributions

3. **Repository Analysis**
   - Assesses code quality
   - Identifies technical skills demonstrated in code
   - Suggests interview questions based on repositories

4. **Cross-Reference & Validation**
   - Matches resume claims with GitHub evidence
   - Identifies skills shown on GitHub but not mentioned in resume
   - Evaluates overall consistency between resume and GitHub profile

## Usage Guide

1. Upload a candidate's resume (PDF or text file)
2. The system will automatically extract the GitHub profile URL
3. If no GitHub URL is found, you'll be prompted to enter it manually
4. The application will analyze the GitHub profile and generate a comprehensive report
5. The report will include an assessment of the candidate's coding skills, activity, and consistency with their resume

## Tips for Best Results

- Use PDFs with proper text extraction (not scanned images)
- Ensure the resume contains a valid GitHub URL (typically in the contact/links section)
- Use a GitHub token to avoid API rate limits when analyzing multiple candidates