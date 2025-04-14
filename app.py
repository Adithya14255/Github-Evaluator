# app.py
import os
import re
import base64
import requests
import fitz  # PyMuPDF
import google.generativeai as genai
from flask import Flask, render_template, request, flash, redirect, url_for, session
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

# Configure API keys
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Optional: For higher API rate limits
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

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
        print(f"Error extracting text from PDF: {e}")
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
    skills_section = re.search(r'(?i)(skills|technologies|technical skills|programming languages)[:\s]*(.*?)(?:\n\n|\Z)', text, re.DOTALL)
    if skills_section:
        skills_text = skills_section.group(2)
        # Extract individual skills
        skills = re.findall(r'([A-Za-z0-9#+\\.]+(?:\s+[A-Za-z0-9#+\\.]+)*)', skills_text)
        return [skill.strip() for skill in skills if len(skill.strip()) > 2]
    return []

def get_github_user_info(github_url):
    """Fetch user profile information from GitHub"""
    username = github_url.rstrip('/').split('/')[-1]
    api_url = f"https://api.github.com/users/{username}"
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    
    response = requests.get(api_url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"GitHub API error: {response.status_code}, {response.text}")
    
    user_data = response.json()
    
    # Get user's repositories
    repos_url = f"https://api.github.com/users/{username}/repos?sort=updated&per_page=10"
    repos_response = requests.get(repos_url, headers=headers)
    repos = repos_response.json() if repos_response.status_code == 200 else []
    
    # Get user's contributions/activity
    events_url = f"https://api.github.com/users/{username}/events/public?per_page=30"
    events_response = requests.get(events_url, headers=headers)
    events = events_response.json() if events_response.status_code == 200 else []
    
    return {
        "profile": user_data,
        "repositories": repos,
        "activity": events
    }

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

def analyze_candidate_with_gemini(github_data, resume_skills, resume_text):
    """Use Gemini API to analyze candidate's GitHub against their resume"""
    # Extract relevant information
    profile = github_data["profile"]
    repos = github_data["repositories"]
    activity = github_data["activity"]
    
    # Create summaries for Gemini
    repo_summary = "\n".join([
        f"- {repo.get('name')}: {repo.get('description') or 'No description'} "
        f"(Language: {repo.get('language') or 'Unknown'}, "
        f"Stars: {repo.get('stargazers_count', 0)}, "
        f"Forks: {repo.get('forks_count', 0)})"
        for repo in repos[:5]  # Limit to top 5 repos
    ])
    
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
    - Username: {profile.get('login')}
    - Name: {profile.get('name') or 'Not provided'}
    - Bio: {profile.get('bio') or 'Not provided'}
    - Location: {profile.get('location') or 'Not provided'}
    - Public repos: {profile.get('public_repos', 0)}
    - Followers: {profile.get('followers', 0)}
    - Following: {profile.get('following', 0)}
    - Account created: {profile.get('created_at')}
    
    Top repositories:
    {repo_summary}
    
    Recent GitHub activity:
    {activity_summary}
    
    Skills claimed in resume:
    {', '.join(resume_skills) if resume_skills else 'No skills extracted'}
    
    Your task:
    1. Analyze whether the GitHub profile provides evidence for the skills claimed in the resume
    2. Identify any additional skills demonstrated on GitHub but not mentioned in the resume
    3. Assess the candidate's coding activity, quality, and consistency based on GitHub data
    4. Evaluate the overall alignment between GitHub profile and resume claims
    
    Provide a detailed analysis in Markdown format with this structure:
    
    ## Candidate Technical Assessment
    
    ### 1. GitHub Profile Summary
    [Provide a concise summary of the candidate's GitHub presence and activity level]
    
    ### 2. Skills Verification
    [Analyze which resume skills are evidenced in their GitHub activity and which lack evidence]
    
    ### 3. Hidden Talents
    [Identify skills demonstrated on GitHub but not claimed on the resume]
    
    ### 4. Code Quality Assessment
    [Evaluate code quality based on repositories and activity]
    
    ### 5. Consistency Analysis
    [Assess consistency between resume claims and GitHub evidence]
    
    ### 6. Recommendations for Interviewer
    [Provide specific areas to probe during technical interviews]
    
    Return ONLY the Markdown text with no preamble or explanation. Ensure formatting is correct with proper Markdown syntax.
    """
    
    # Call Gemini API
    model = genai.GenerativeModel('gemini-1.5-pro')
    response = model.generate_content(prompt)
    
    return response.text

def analyze_repo_with_gemini(repo_data, resume_skills):
    """Use Gemini API to analyze a specific repository against skills"""
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
    model = genai.GenerativeModel('gemini-1.5-pro')
    response = model.generate_content(prompt)
    
    return response.text

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Check if a resume file was uploaded
        if 'resume' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
        
        file = request.files['resume']
        
        # If user does not select file, browser also
        # submit an empty part without filename
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
                github_url = extract_github_profile(resume_text)
                
                # Extract skills from resume
                skills = extract_skills_from_resume(resume_text)
                
                if not github_url:
                    manual_github = request.form.get('github_url')
                    if manual_github and 'github.com' in manual_github:
                        github_url = manual_github
                    else:
                        flash('No GitHub profile found in resume. Please enter it manually.', 'warning')
                        return render_template('manual_github.html', resume_path=file_path)
                
                # Store information in session for later use
                session['resume_path'] = file_path
                session['github_url'] = github_url
                session['resume_skills'] = skills
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
    resume_text = ""
    if resume_path.lower().endswith('.pdf'):
        resume_text = extract_text_from_pdf(resume_path)
    elif resume_path.lower().endswith('.txt'):
        with open(resume_path, 'r', encoding='utf-8') as f:
            resume_text = f.read()
    
    skills = extract_skills_from_resume(resume_text)
    
    # Store information in session
    session['resume_path'] = resume_path
    session['github_url'] = github_url
    session['resume_skills'] = skills
    session['resume_text'] = resume_text
    
    return redirect(url_for('analyze'))

@app.route('/analyze')
def analyze():
    # Retrieve information from session
    resume_path = session.get('resume_path')
    github_url = session.get('github_url')
    resume_skills = session.get('resume_skills', [])
    resume_text = session.get('resume_text', '')
    
    if not github_url or not resume_path:
        flash('Missing required information. Please upload resume again.', 'danger')
        return redirect(url_for('index'))
    
    try:
        # Show loading message
        flash('Analyzing GitHub profile. This may take a minute...', 'info')
        
        # Fetch GitHub profile information
        github_data = get_github_user_info(github_url)
        
        # Analyze with Gemini
        analysis_result = analyze_candidate_with_gemini(github_data, resume_skills, resume_text)
        
        # Get top repository for detailed analysis
        top_repo = None
        if github_data["repositories"]:
            # Sort by stars and pick the top one
            sorted_repos = sorted(github_data["repositories"], 
                                 key=lambda x: x.get('stargazers_count', 0), 
                                 reverse=True)
            top_repo = sorted_repos[0]
            repo_url = top_repo.get('html_url')
            
            # Get repo contents
            repo_data = get_repo_contents(repo_url)
            
            # Analyze repository
            repo_analysis = analyze_repo_with_gemini(repo_data, resume_skills)
        else:
            repo_analysis = "No repositories found for detailed analysis."
        
        # Extract GitHub profile info for display
        profile = github_data["profile"]
        username = profile.get('login')
        name = profile.get('name') or username
        avatar_url = profile.get('avatar_url')
        bio = profile.get('bio') or "No bio available"
        
        return render_template('result.html', 
                              analysis=analysis_result,
                              repo_analysis=repo_analysis,
                              github_url=github_url,
                              resume_skills=resume_skills,
                              username=username,
                              name=name,
                              bio=bio,
                              avatar_url=avatar_url)
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('index'))

@app.route('/repo/<username>/<repo_name>')
def analyze_repo(username, repo_name):
    # Retrieve information from session
    resume_skills = session.get('resume_skills', [])
    
    repo_url = f"https://github.com/{username}/{repo_name}"
    
    try:
        # Show loading message
        flash(f'Analyzing repository: {repo_name}. This may take a minute...', 'info')
        
        # Get repo contents
        repo_data = get_repo_contents(repo_url)
        
        # Analyze repository
        repo_analysis = analyze_repo_with_gemini(repo_data, resume_skills)
        
        return render_template('repo_analysis.html', 
                              analysis=repo_analysis,
                              repo_url=repo_url,
                              repo_name=repo_name)
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('analyze'))

if __name__ == '__main__':
    app.run(debug=True)