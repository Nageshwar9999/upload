import os
from github import Github
from flask import Flask, request, redirect, url_for, render_template, session, flash, send_file
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from io import BytesIO

# Load environment variables from .env file
load_dotenv()

# Flask app setup
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")  
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
GITHUB_REPO = os.getenv('GITHUB_REPO')

# Database configuration
DB_CONFIG = {
    "host": os.getenv('DB_HOST'),
    "user": os.getenv('DB_USER'),
    "password": os.getenv('DB_PASSWORD'),
    "database": os.getenv('DB_NAME'),
    "port": int(os.getenv('DB_PORT', 3306))
}

# Database connection function
def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"Database Connection Error: {e}")
        return None

# Check user credentials (Login)
def check_credentials(key, password):
    conn = get_db_connection()
    if conn is None:
        return None
    cursor = conn.cursor()
    cursor.execute('SELECT password FROM login WHERE `key` = %s', (key,))
    user = cursor.fetchone()
    conn.close()
    if user and check_password_hash(user[0], password):
        return True
    return False

# Add user to database (Signup)
def add_user(key, password):
    conn = get_db_connection()
    if conn is None:
        return
    cursor = conn.cursor()
    hashed_password = generate_password_hash(password)
    try:
        cursor.execute('INSERT INTO login (`key`, `password`) VALUES (%s, %s)', (key, hashed_password))
        conn.commit()
    except Error as e:
        print(f"Database Insert Error: {e}")
    conn.close()

# Add document info to database
def add_document_to_db(key, filename):
    conn = get_db_connection()
    if conn is None:
        return
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO file (`key`, filename) VALUES (%s, %s)', (key, filename))
        conn.commit()
    except Error as e:
        print(f"Database Insert Error: {e}")
    conn.close()

# Retrieve documents from database
def get_documents(key):
    conn = get_db_connection()
    if conn is None:
        return []
    cursor = conn.cursor()
    cursor.execute('SELECT filename FROM file WHERE `key` = %s', (key,))
    files = cursor.fetchall()
    conn.close()
    return [file[0] for file in files]

# Upload file to GitHub
def upload_to_github(file, filename):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("Error: GitHub token or repository is not set in environment variables.")
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    content = file.read()
    file.seek(0)  # Reset file pointer after reading
    try:
        repo.create_file(f"uploads/{filename}", "Upload file", content, branch="main")
    except Exception as e:
        print(f"GitHub upload error: {e}")

# Download file from GitHub
def download_from_github(filename):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("Error: GitHub token or repository is not set in environment variables.")
        return None

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    try:
        file = repo.get_contents(f"uploads/{filename}", ref="main")
        return BytesIO(file.decoded_content)
    except Exception as e:
        print(f"GitHub download error: {e}")
        return None

# Delete file from GitHub
def delete_from_github(filename):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("Error: GitHub token or repository is not set in environment variables.")
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    try:
        file_path = f"uploads/{filename}"
        file = repo.get_contents(file_path, ref="main")
        repo.delete_file(file_path, "Deleting file", file.sha, branch="main")
    except Exception as e:
        print(f"GitHub delete error: {e}")

# Delete document from the database
def delete_document_from_db(key, filename):
    conn = get_db_connection()
    if conn is None:
        return
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM file WHERE `key` = %s AND filename = %s', (key, filename))
        conn.commit()
    except Error as e:
        print(f"Database Delete Error: {e}")
    conn.close()

# Flask routes
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'signup':
            key = request.form['key']
            password = request.form['password']
            confirm = request.form['confirmPassword']

            if password != confirm:
                flash("Passwords do not match.", "danger")
                return render_template('index.html')

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM login WHERE `key` = %s", (key,))
            if cursor.fetchone():
                flash("Key already exists. Choose a different one.", "warning")
                conn.close()
                return render_template('index.html')

            add_user(key, password)
            conn.close()
            flash("Signup successful! You can now login.", "success")
            return redirect(url_for('index'))

        elif action == 'login':
            key = request.form['key']
            password = request.form['password']

            if check_credentials(key, password):
                session['user'] = key
                flash("Login successful!", "success")
                return redirect(url_for('dashboard'))
            else:
                flash("Invalid credentials.", "danger")
                return render_template('index.html')

    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for('index'))

    user = session['user']
    documents = get_documents(user)
    return render_template('dashboard.html', user=user, documents=documents)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'user' not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for('index'))

    file = request.files['file']
    if file:
        filename = secure_filename(file.filename)
        upload_to_github(file, filename)
        add_document_to_db(session['user'], filename)
        flash(f"File '{filename}' uploaded successfully!", "success")

    return redirect(url_for('dashboard'))

@app.route('/view/<filename>')
def view_file(filename):
    file_stream = download_from_github(filename)
    if file_stream:
        return send_file(file_stream, download_name=filename, as_attachment=False)
    flash("File not found.", "danger")
    return redirect(url_for('dashboard'))

@app.route('/delete/<filename>', methods=['POST'])
def delete_file(filename):
    if 'user' not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for('index'))

    delete_from_github(filename)
    delete_document_from_db(session['user'], filename)
    flash(f"File '{filename}' has been deleted successfully.", "success")
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("You have been logged out.", "info")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
