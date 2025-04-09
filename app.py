# app.py
import os
import base64
import requests
import google.generativeai as genai
from flask import Flask, render_template, request, flash, redirect, url_for
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

# Configure API keys
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Optional: For higher API rate limits
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

app = Flask(__name__)
app.secret_key = os.urandom(24)

def get_repo_contents(repo_url):
    """Fetch repository files from GitHub"""
    # Extract owner and repo name from GitHub URL
    # Example: https://github.com/username/repo
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

def analyze_code_with_gemini(repo_data):
    """Use Gemini API to analyze code"""
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
    You are a senior software developer reviewing code quality. Analyze this GitHub repository:
    
    Repository: {repo_name}
    Description: {repo_description}
    
    Files in the repository:
    {file_list}
    
    {file_contents}
    
    Provide a detailed code review analysis in Markdown format with this exact structure:
    
    ## Code Review: {repo_name}
    
    ### 1. Code Quality
    * **Readability:** [Assess code readability, formatting, naming conventions]
    * **Maintainability:** [Evaluate how easy the code is to maintain]
    * **Testability:** [Comment on the testability of the code]
    
    ### 2. Best Practices
    * **Documentation:** [Evaluate documentation quality and completeness]
    * **Version Control:** [Comment on version control practices]
    * **Error Handling:** [Analyze error handling approaches]
    * **Dependency Management:** [Assess how dependencies are managed]
    
    ### 3. Architecture
    * **Frontend:** [Evaluate frontend architecture]
    * **Backend:** [Evaluate backend architecture]
    * **Deployment:** [Comment on deployment configuration]
    
    ### 4. Potential Bugs
    [List specific potential bugs or issues found in the code]
    
    ### 5. Security Concerns
    [Identify potential security vulnerabilities]
    
    ### 6. Performance
    [Evaluate potential performance issues and bottlenecks]
    
    ### 7. Recommendations
    [Provide specific, actionable recommendations for improvement]
    
    Return ONLY the Markdown text with no preamble or explanation. Ensure formatting is correct with proper Markdown syntax.
    """
    
    # Call Gemini API
    model = genai.GenerativeModel('gemini-1.5-pro')
    response = model.generate_content(prompt)
    
    return response.text

@app.route('/', methods=['GET', 'POST'])
def index():
    analysis_result = None
    repo_url = ""
    
    if request.method == 'POST':
        repo_url = request.form.get('repo_url')
        if not repo_url:
            flash('Please enter a GitHub repository URL', 'danger')
            return redirect(url_for('index'))
        
        try:
            # Show loading message
            flash('Analyzing repository. This may take a minute...', 'info')
            
            # Fetch repository contents
            repo_data = get_repo_contents(repo_url)
            
            # Analyze with Gemini
            analysis_result = analyze_code_with_gemini(repo_data)
            
            # Extract repo info for display
            repo_info = repo_data["repo_info"]
            repo_name = repo_info.get("name", "Unknown")
            repo_owner = repo_info.get("owner", {}).get("login", "Unknown")
            
            return render_template('result.html', 
                                  analysis=analysis_result,
                                  repo_url=repo_url,
                                  repo_name=repo_name,
                                  repo_owner=repo_owner)
            
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('index'))
    
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)