# app.py
import os
import re
import base64
import requests
import fitz  # PyMuPDF
import google.generativeai as genai
import logging
from flask import Flask, render_template, request, flash, redirect, url_for, session, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from urllib.parse import urlparse
from datetime import datetime

load_dotenv()  # Load environment variables from .env file

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configure API keys
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Optional: For higher API rate limits
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_REGION = os.getenv("GOOGLE_CLOUD_REGION")
SUMMARY_MODEL_NAME = os.getenv('SUMMARY_MODEL', 'gemini-pro')
ANALYSIS_MODEL_NAME = os.getenv('ANALYSIS_MODEL', 'gemini-1.5-pro')
MAX_FILES_PER_REPO = int(os.getenv('MAX_FILES_PER_REPO', 5))
MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 2000))

# Configure Gemini API
def configure_genai():
    """Configure Google Generative AI"""
    try:
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            logging.info("Configured genai with API key")
        elif GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_REGION:
            genai.configure(project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_REGION)
            logging.info("Configured genai with project and location")
        else:
            genai.configure()  # Use defaults
            logging.info("Configured genai with default settings")
        return True
    except Exception as e:
        logging.error(f"Error configuring genai: {e}")
        return False

# Initialize genai
genai_configured = configure_genai()

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# Create uploads folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(pdf_path):
    """Extract text from PDF file"""
    text = ""
    try:
        with fitz.open(pdf_path) as doc:
            for page in doc:
                text += page.get_text()
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
    return text

def extract_github_profile(text):
    """Extract GitHub profile URL from text"""
    # Common patterns for GitHub profiles in resumes
    patterns = [
        r'github\.com/([a-zA-Z0-9_-]+)/?',
        r'github\.com/([^/\s]+)',
        r'github:\s*([a-zA-Z0-9_-]+)',
        r'GitHub:\s*https?://github\.com/([a-zA-Z0-9_-]+)/?',
        r'GitHub\s*Profile:?\s*https?://github\.com/([a-zA-Z0-9_-]+)/?'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            username = match.group(1)
            return f"https://github.com/{username}"
    
    return None

def extract_skills_from_resume(text):
    """Extract skills mentioned in the resume"""
    # Simplified skill extraction using common patterns
    skills_section = re.search(r'(?i)(skills|technologies|technical skills|programming languages)[:\s]*(.*?)(?=\n[A-Z][a-z]|$)', text, re.DOTALL)
    if skills_section:
        skills_text = skills_section.group(2)
        # Extract individual skills
        skills = re.findall(r'([A-Za-z0-9#+\\.]+(?:\s+[A-Za-z0-9#+\\.]+)*)', skills_text)
        return [skill.strip() for skill in skills if len(skill.strip()) > 2]
    return []

def fetch_github_data(github_url):
    """Fetch user profile information from GitHub"""
    username = github_url.rstrip('/').split('/')[-1]
    api_url = f"https://api.github.com/users/{username}"
    repos_url = f"https://api.github.com/users/{username}/repos?sort=updated&per_page=100"
    
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    
    try:
        profile_response = requests.get(api_url, headers=headers)
        profile_response.raise_for_status()
        profile_data = profile_response.json()
        
        repos_response = requests.get(repos_url, headers=headers)
        repos_response.raise_for_status()
        repos_data = repos_response.json()
        
        # Get user's contributions/activity
        events_url = f"https://api.github.com/users/{username}/events/public?per_page=30"
        events_response = requests.get(events_url, headers=headers)
        events = events_response.json() if events_response.status_code == 200 else []
        
        return {
            "profile": profile_data,
            "repositories": repos_data,
            "activity": events
        }
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching data from GitHub: {e}")
        raise Exception(f"GitHub API error: {str(e)}")
    except ValueError as e:
        logging.error(f"Error parsing GitHub response: {e}")
        raise Exception(f"Error parsing GitHub data: {str(e)}")

def get_contributed_repos(username):
    """Fetch repositories the user has contributed to."""
    api_url = f"https://api.github.com/users/{username}/events/public?per_page=100"
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    try:
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        events = response.json()
        contributed_repos = set()
        for event in events:
            if event['type'] in ['PushEvent', 'PullRequestEvent'] and 'repo' in event:
                repo_url = event['repo']['url']
                # Convert API URL to HTML URL
                contributed_repos.add(repo_url.replace("api.github.com/repos/", "github.com/"))

        return list(contributed_repos)
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching contributed repos: {e}")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return []

def get_repo_contents(repo_url):
    """Fetch repository files from GitHub"""
    # Extract owner and repo name from GitHub URL
    parts = repo_url.rstrip('/').split('/')
    if 'github.com' not in repo_url or len(parts) < 5:
        raise ValueError("Invalid GitHub repository URL")
    
    owner = parts[-2]
    repo = parts[-1]
    
    # API endpoints
    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/main?recursive=1"
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    
    # Get file tree
    response = requests.get(api_url, headers=headers)
    if response.status_code == 404:
        # Try 'master' branch if 'main' doesn't exist
        api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/master?recursive=1"
        response = requests.get(api_url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"GitHub API error: {response.status_code}, {response.text}")
    
    tree = response.json().get('tree', [])
    
    # Filter only code files (ignore binaries, images, etc.)
    code_extensions = ['.py', '.js', '.html', '.css', '.java', '.cpp', '.c', '.h', '.go', '.rb', 
                       '.php', '.ts', '.jsx', '.tsx', '.md', '.json', '.yml', '.yaml', '.xml','.txt']
    
    code_files = []
    total_size = 0
    
    for item in tree:
        if item['type'] == 'blob':
            file_path = item['path']
            file_ext = os.path.splitext(file_path)[1].lower()
            if any(file_path.endswith(ext) for ext in code_extensions):
                if total_size + item['size'] <= 500000:  # Limit to ~500KB total
                    code_files.append(item)
                    total_size += item['size']
    
    # Fetch content for each file
    files_content = {}
    for file in code_files[:20]:  # Limit number of files to prevent API abuse
        content_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file['path']}"
        content_response = requests.get(content_url, headers=headers)
        
        if content_response.status_code == 200:
            content_data = content_response.json()
            if 'content' in content_data:
                try:
                    decoded_content = base64.b64decode(content_data['content']).decode('utf-8')
                    files_content[file['path']] = decoded_content
                except UnicodeDecodeError:
                    # Skip files that can't be decoded as text
                    pass
    
    # Get repository info
    repo_info_url = f"https://api.github.com/repos/{owner}/{repo}"
    repo_info_response = requests.get(repo_info_url, headers=headers)
    repo_info = repo_info_response.json() if repo_info_response.status_code == 200 else {}
    
    return {
        "repo_info": repo_info,
        "files": files_content
    }

def get_repo_file_summaries(repo_url, max_files=MAX_FILES_PER_REPO):
    """Fetch and summarize key files from a repository."""
    if not genai_configured:
        return {"error": "Gemini API not configured"}
        
    repo_name = urlparse(repo_url).path.split('/')[-1]
    logging.info(f"Fetching and summarizing files for repo: {repo_name}")
    
    try:
        repo_data = get_repo_contents(repo_url)
        if not repo_data or not repo_data.get("files"):
            return {"repo_name": repo_name, "summaries": {}}
            
        files = repo_data["files"]
        # Sort files by size (largest first), then take top N
        sorted_files = sorted(files.items(), key=lambda x: len(x[1]), reverse=True)[:max_files]
        
        file_summaries = {}
        for filename, content in sorted_files:
            logging.info(f"  Processing file: {filename}")
            file_summaries[filename] = summarize_file_content(content, filename)
            
        return {"repo_name": repo_name, "summaries": file_summaries}
    except Exception as e:
        logging.error(f"Error getting repo file summaries: {e}")
        return {"repo_name": repo_name, "error": str(e)}

def summarize_file_content(file_content, filename, max_length=MAX_CONTENT_LENGTH):
    """Summarize the content of a code file using Gemini."""
    if not genai_configured:
        return "Error: Gemini API not configured"

    try:
        # Truncate file content to the maximum length
        truncated_content = file_content[:max_length]
        prompt = f"Summarize the following code from file '{filename}'. Focus on the key functions, classes, and overall purpose. Identify the main technologies and programming concepts demonstrated:\n\n```\n{truncated_content}\n```"
        model = genai.GenerativeModel(SUMMARY_MODEL_NAME)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.error(f"Error summarizing file content: {e}")
        return f"Error summarizing file: {e}"

def analyze_candidate_with_gemini(github_data, contributed_repos, repo_summaries, resume_skills, resume_text):
    """Use Gemini API to analyze candidate's GitHub against their resume"""
    if not genai_configured:
        return "Error: Gemini API not configured"

    # Extract relevant information
    profile = github_data["profile"]
    repos = github_data["repositories"]
    activity = github_data["activity"]
    
    # Basic user info
    user_info = f"Name: {profile.get('name', 'N/A')}, Location: {profile.get('location', 'N/A')}, Followers: {profile.get('followers', 0)}, Following: {profile.get('following', 0)}, Bio: {profile.get('bio', 'N/A')}"
    
    # Owned Repositories Summary
    owned_repo_list = "\n".join([
        f"- {repo.get('name', 'N/A')}: Stars: {repo.get('stargazers_count', 0)}, Forks: {repo.get('forks_count', 0)}, Description: {repo.get('description', 'N/A')}, Main Language: {repo.get('language', 'N/A')}"
        for repo in repos
    ])
    owned_repo_summary = f"The candidate has {len(repos)} owned repositories:\n{owned_repo_list}"
    
    # Contributed Repositories Summary
    contributed_repo_list = "\n".join([f"- {repo}" for repo in contributed_repos[:5]]) if contributed_repos else "None"
    contributed_repo_summary = f"The candidate has contributed to the following repositories (limited to the top 5):\n{contributed_repo_list}"
    
    # Repository File Summaries
    repo_summary_text = ""
    for repo_data in repo_summaries:
        repo_name = repo_data.get('repo_name', 'Unknown Repo')
        repo_summary_text += f"\n\n### Summary of {repo_name}:\n"
        if repo_data.get('summaries'):
            for filename, summary in repo_data['summaries'].items():
                repo_summary_text += f"- {filename}: {summary}\n"
        else:
            repo_summary_text += "No file summaries available for this repository.\n"
    
    # Analyze recent activity
    activity_types = {}
    for event in activity[:30]:  # Look at most recent 30 events
        event_type = event.get('type', 'Unknown')
        activity_types[event_type] = activity_types.get(event_type, 0) + 1
    
    activity_summary = "\n".join([
        f"- {activity_type}: {count} times" 
        for activity_type, count in activity_types.items()
    ])
    
    # Construct the prompt for Gemini
    prompt = f"""
    You are an expert technical recruiter analyzing a candidate's GitHub profile against their resume.
    
    GitHub Profile:
    {user_info}
    
    Owned Repositories:
    {owned_repo_summary}
    
    Contributed Repositories:
    {contributed_repo_summary}
    
    Recent GitHub activity:
    {activity_summary}
    
    Summaries of Key Files from Repositories:
    {repo_summary_text}
    
    Skills claimed in resume:
    {', '.join(resume_skills) if resume_skills else 'No skills extracted'}
    
    Resume Text:
    {resume_text}
    
    Your task:
    1. Analyze whether the GitHub profile provides evidence for the skills claimed in the resume
    2. Identify any additional skills demonstrated on GitHub but not mentioned in the resume
    3. Assess the candidate's coding activity, quality, and consistency based on GitHub data
    4. Evaluate the overall alignment between GitHub profile and resume claims
    
    Include an overall rating of the candidate's GitHub profile and resume, using a scale of 1 to 5 (1 being the lowest, 5 being the highest). Justify the rating with specific observations.
    
    Provide a detailed analysis in Markdown format with this structure:
    
    ## Candidate Technical Assessment
    
    ### 1. GitHub Profile Summary
    [Provide a concise summary of the candidate's GitHub presence and activity level]
    
    ### 2. Skills Verification
    [Analyze which resume skills are evidenced in their GitHub activity and which lack evidence]
    
    ### 3. Hidden Talents
    [Identify skills demonstrated on GitHub but not claimed on the resume]
    
    ### 4. Consistency Analysis
    [Assess consistency between resume claims and GitHub evidence]
    
    ### 5. Overall Rating
    [Provide a rating from 1-5 and justify with specific observations]
    
    ### 6. Recommendations for Interviewer
    [Provide specific areas to probe during technical interviews]
    
    Return ONLY the Markdown text with no preamble or explanation. Ensure formatting is correct with proper Markdown syntax.
    """
    
    # Call Gemini API
    try:
        model = genai.GenerativeModel(ANALYSIS_MODEL_NAME)
        response = model.generate_content(prompt)
        analysis_text = response.text
        
        # Extract rating and rationale
        rating, rationale = extract_rating_and_rationale(analysis_text)
        
        return analysis_text, rating, rationale
    except Exception as e:
        error_message = f"Error during Gemini analysis: {e}"
        logging.error(error_message)
        return error_message, None, None

def analyze_repo_with_gemini(repo_data, resume_skills):
    """Use Gemini API to analyze a specific repository against skills"""
    if not genai_configured:
        return "Error: Gemini API not configured"
        
    # Prepare data for analysis
    repo_name = repo_data["repo_info"].get("name", "Unknown")
    repo_description = repo_data["repo_info"].get("description", "No description available")
    files = repo_data["files"]
    
    # Create a summary of the repository for Gemini
    file_list = "\n".join([f"- {filename}" for filename in files.keys()])
    
    # Create a condensed version of files to send to Gemini
    file_contents = ""
    for filename, content in files.items():
        # Limit each file to prevent exceeding token limits
        truncated_content = content[:5000] + "..." if len(content) > 5000 else content
        file_contents += f"\n\n### File: {filename}\n```\n{truncated_content}\n```"
    
    # Construct the prompt for Gemini
    prompt = f"""
    You are a senior technical recruiter evaluating a candidate's repository against their claimed skills.
    
    Repository: {repo_name}
    Description: {repo_description}
    
    Files in the repository:
    {file_list}
    
    Skills claimed in resume:
    {', '.join(resume_skills) if resume_skills else 'No skills extracted'}
    
    {file_contents}
    
    Provide a detailed repository analysis in Markdown format with this structure:
    
    ## Repository Analysis: {repo_name}
    
    ### 1. Technical Skills Demonstrated
    [List and evaluate the technical skills evident in this repository]
    
    ### 2. Code Quality Assessment
    * **Architecture:** [Evaluate overall architecture and organization]
    * **Best Practices:** [Assess adherence to coding best practices]
    * **Error Handling:** [Analyze error handling approaches]
    * **Documentation:** [Evaluate code comments and documentation]
    
    ### 3. Skill Verification
    [Compare skills demonstrated in the repository with those claimed in the resume]
    
    ### 4. Technical Sophistication
    [Assess the level of technical complexity and sophistication]
    
    ### 5. Potential Interview Questions
    [Suggest specific technical questions based on this repository to ask during interviews]
    
    Return ONLY the Markdown text with no preamble or explanation. Ensure formatting is correct with proper Markdown syntax.
    """
    
    # Call Gemini API
    try:
        model = genai.GenerativeModel('gemini-1.5-pro')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.error(f"Error analyzing repo with Gemini: {e}")
        return f"Error analyzing repository: {str(e)}"

def extract_rating_and_rationale(text):
    """
    Extracts the rating and rationale from the Gemini-generated text.
    """
    rating_match = re.search(r"Overall Rating:\s*(\d+)/5", text)
    rationale_match = re.search(r"Rationale:\s*(.+)", text, re.DOTALL)  # Capture multiline

    if rating_match and rationale_match:
        rating = int(rating_match.group(1))
        rationale = rationale_match.group(1).strip()
        return rating, rationale
    else:
        return None, None

def get_rating_badge(rating):
    """
    Gets the appropriate HTML badge based on the rating.
    """
    if rating is None:
        return '<span class="badge bg-secondary">Not Rated</span>'
    elif 4 <= rating <= 5:
        return f'<span class="rating-box"><i class="fas fa-thumbs-up"></i> Excellent ({rating}/5)</span>'
    elif 3 == rating:
        return f'<span class="rating-box neutral"><i class="fas fa-meh"></i> Good ({rating}/5)</span>'
    elif 2 == rating:
        return f'<span class="rating-box negative"><i class="fas fa-thumbs-down"></i> Fair ({rating}/5)</span>'
    elif 1 == rating:
        return f'<span class="rating-box negative"><i class="fas fa-exclamation-triangle"></i> Poor ({rating}/5)</span>'
    else:
        return '<span class="badge bg-secondary">Invalid Rating</span>'

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Check if a resume file was uploaded
        if 'resume' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
        
        file = request.files['resume']
        github_url = request.form.get('github_url')
        
        if file.filename == '' and github_url and 'github.com' in github_url:
            # If user only provides GitHub URL, store it and redirect
            username = github_url.rstrip('/').split('/')[-1]
            session['github_url'] = github_url
            session['github_username'] = username
            return redirect(url_for('analyze'))
            
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            try:
                # Extract text from resume
                resume_text = ""
                if filename.lower().endswith('.pdf'):
                    resume_text = extract_text_from_pdf(file_path)
                elif filename.lower().endswith('.txt'):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        resume_text = f.read()
                
                # Extract GitHub profile from resume
                extracted_github_url = extract_github_profile(resume_text)
                
                # Extract skills from resume
                skills = extract_skills_from_resume(resume_text)
                
                # Use provided GitHub URL or extracted one
                github_url = github_url if github_url and 'github.com' in github_url else extracted_github_url
                
                if not github_url:
                    flash('No GitHub profile found in resume. Please enter it manually.', 'warning')
                    session['resume_path'] = file_path
                    session['resume_text'] = resume_text
                    session['extracted_skills'] = skills
                    return render_template('manual_github.html', resume_path=file_path)
                
                # Store information in session for later use
                username = github_url.rstrip('/').split('/')[-1]
                session['resume_path'] = file_path
                session['github_url'] = github_url
                session['github_username'] = username
                session['extracted_skills'] = skills
                session['resume_text'] = resume_text
                
                # Redirect to analysis page
                return redirect(url_for('analyze'))
                
            except Exception as e:
                flash(f'Error processing file: {str(e)}', 'danger')
                return redirect(request.url)
        else:
            flash('File type not allowed. Please upload PDF, DOCX, or TXT files.', 'danger')
            return redirect(request.url)
    
    return render_template('index.html')

@app.route('/manual_github', methods=['POST'])
def manual_github():
    github_url = request.form.get('github_url')
    resume_path = request.form.get('resume_path')
    
    if not github_url or 'github.com' not in github_url:
        flash('Please enter a valid GitHub URL', 'danger')
        return render_template('manual_github.html', resume_path=resume_path)
    
    # Retrieve or re-extract resume text and skills
    resume_text = session.get('resume_text', '')
    skills = session.get('extracted_skills', [])
    
    if not resume_text and resume_path:
        if resume_path.lower().endswith('.pdf'):
            resume_text = extract_text_from_pdf(resume_path)
        elif resume_path.lower().endswith('.txt'):
            with open(resume_path, 'r', encoding='utf-8') as f:
                resume_text = f.read()
        skills = extract_skills_from_resume(resume_text)
    
    # Store information in session
    username = github_url.rstrip('/').split('/')[-1]
    session['resume_path'] = resume_path
    session['github_url'] = github_url
    session['github_username'] = username
    session['extracted_skills'] = skills
    session['resume_text'] = resume_text
    
    return redirect(url_for('analyze'))

@app.route('/analyze')
def analyze():
    # Retrieve information from session
    resume_path = session.get('resume_path')
    github_url = session.get('github_url')
    github_username = session.get('github_username')
    resume_skills = session.get('extracted_skills', [])
    resume_text = session.get('resume_text', '')
    
    if not github_url and not github_username:
        flash('Missing GitHub information. Please upload resume or provide GitHub URL again.', 'danger')
        return redirect(url_for('index'))
    
    try:
        # Show loading message
        flash('Analyzing GitHub profile. This may take a minute...', 'info')
        
        # Fetch GitHub profile information
        github_data = fetch_github_data(github_url if github_url else f"https://github.com/{github_username}")
        
        # Get contributed repositories
        username = github_username if github_username else github_url.rstrip('/').split('/')[-1]
        contributed_repos = get_contributed_repos(username)
        
        # Get repository file summaries
        repo_summaries = []
        top_repos = sorted(github_data["repositories"], 
                           key=lambda x: x.get('stargazers_count', 0), 
                           reverse=True)[:3]  # Get top 3 repos
                           
        for repo in top_repos:
            repo_url = repo.get('html_url')
            if repo_url:
                repo_summaries.append(get_repo_file_summaries(repo_url))
        
        # Analyze with Gemini
        analysis_result, rating, rationale = analyze_candidate_with_gemini(
            github_data, contributed_repos, repo_summaries, resume_skills, resume_text
        )
        
        # Get detailed analysis of top repository
        repo_analysis = ""
        if top_repos:
            top_repo = top_repos[0]
            repo_url = top_repo.get('html_url')
            repo_data = get_repo_contents(repo_url)
            repo_analysis = analyze_repo_with_gemini(repo_data, resume_skills)
        
        # Extract GitHub profile info for display
        profile = github_data["profile"]
        username = profile.get('login')
        name = profile.get('name') or username
        avatar_url = profile.get('avatar_url')
        bio = profile.get('bio') or "No bio available"
        rating_badge = get_rating_badge(rating)
        
        return render_template('result.html', 
                              analysis=analysis_result,
                              repo_analysis=repo_analysis,
                              github_url=github_url,
                              resume_skills=resume_skills,
                              username=username,
                              name=name,
                              bio=bio,
                              avatar_url=avatar_url,
                              rating=rating_badge,
                              rationale=rationale)
            
    except Exception as e:
        error_message = f'Error: {str(e)}'
        logging.error(error_message)
        flash(error_message, 'danger')
        return render_template('error.html', message=error_message), 500

@app.route('/error')
def error():
    message = request.args.get('message', 'An unknown error occurred')
    return render_template('error.html', message=message)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))