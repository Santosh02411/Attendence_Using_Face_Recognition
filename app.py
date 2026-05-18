import os
import sqlite3
import base64
import io
import csv
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, flash
import cv2
import numpy as np
from PIL import Image

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'Datasets')
RECOGNIZER_DIR = os.path.join(BASE_DIR, 'recognizer')
DATABASE_PATH = os.path.join(BASE_DIR, 'database', 'app.db')
FACE_DATABASE_PATH = os.path.join(BASE_DIR, 'database', 'FaceBase.db')
CASCADE_PATH = os.path.join(BASE_DIR, 'haarcascade_frontalface_default.xml')

app = Flask(__name__)
app.secret_key = 'change-this-secret-key'


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_databases():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RECOGNIZER_DIR, exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'database'), exist_ok=True)

    if not os.path.exists(CASCADE_PATH):
        if hasattr(cv2, 'data'):
            src = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
            if os.path.exists(src):
                with open(src, 'rb') as src_file:
                    with open(CASCADE_PATH, 'wb') as dst_file:
                        dst_file.write(src_file.read())

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY,
        name TEXT,
        roll_no TEXT,
        branch TEXT,
        semester TEXT,
        password TEXT,
        photo_count INTEGER DEFAULT 0
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS subjects(
        id INTEGER PRIMARY KEY,
        name TEXT,
        code TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY,
        subject_id INTEGER,
        title TEXT,
        date TEXT,
        time TEXT,
        end_date TEXT,
        end_time TEXT,
        is_recurring INTEGER DEFAULT 0,
        active INTEGER DEFAULT 0,
        FOREIGN KEY(subject_id) REFERENCES subjects(id)
    )''')
    
    # Migration for sessions table
    cursor.execute("PRAGMA table_info(sessions)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'end_date' not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN end_date TEXT")
    if 'end_time' not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN end_time TEXT")
    if 'is_recurring' not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN is_recurring INTEGER DEFAULT 0")

    cursor.execute('''CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY,
        student_id INTEGER,
        session_id INTEGER,
        status TEXT,
        timestamp TEXT,
        note TEXT,
        FOREIGN KEY(student_id) REFERENCES students(id),
        FOREIGN KEY(session_id) REFERENCES sessions(id)
    )''')
    cursor.execute('SELECT 1 FROM admins LIMIT 1')
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO admins(username, password) VALUES(?, ?)', ('admin', 'admin123'))
    conn.commit()
    conn.close()

    if not os.path.exists(FACE_DATABASE_PATH):
        face_conn = sqlite3.connect(FACE_DATABASE_PATH)
        face_cursor = face_conn.cursor()
        face_cursor.execute('''CREATE TABLE IF NOT EXISTS people(
            id INTEGER PRIMARY KEY,
            name TEXT,
            gender TEXT,
            section TEXT
        )''')
        face_conn.commit()
        face_conn.close()


def query_db(query, args=(), one=False):
    conn = get_db_connection()
    cursor = conn.execute(query, args)
    rows = cursor.fetchall()
    conn.close()
    return rows[0] if one and rows else rows


def execute_db(query, args=()):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, args)
    conn.commit()
    lastrowid = cursor.lastrowid
    conn.close()
    return lastrowid


def create_lbph_recognizer():
    if hasattr(cv2, 'face'):
        face = cv2.face
        if hasattr(face, 'LBPHFaceRecognizer_create'):
            return face.LBPHFaceRecognizer_create()
        if hasattr(face, 'createLBPHFaceRecognizer'):
            return face.createLBPHFaceRecognizer()
    raise RuntimeError('OpenCV LBPH face recognizer is unavailable. Install opencv-contrib-python.')


def get_face_profile(student_id):
    conn = sqlite3.connect(FACE_DATABASE_PATH)
    cursor = conn.execute('SELECT id, name, gender, section FROM people WHERE id=?', (student_id,))
    profile = cursor.fetchone()
    conn.close()
    return profile


def save_face_profile(student_id, name, gender, section):
    conn = sqlite3.connect(FACE_DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM people WHERE id=?', (student_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO people(id, name, gender, section) VALUES(?, ?, ?, ?)', (student_id, name, gender, section))
    else:
        cursor.execute('UPDATE people SET name=?, gender=?, section=? WHERE id=?', (name, gender, section, student_id))
    conn.commit()
    conn.close()


def parse_base64_image(data):
    if ',' in data:
        data = data.split(',', 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data)))


def save_face_images(student_id, images):
    existing_images = [name for name in os.listdir(DATA_DIR) if name.startswith(f'User.{student_id}.')]
    count = len(existing_images)
    saved = 0
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    for image_data in images:
        image = parse_base64_image(image_data)
        frame = np.array(image.convert('RGB'))
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
        if len(faces) > 0:
            faces = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)
            (x, y, w, h) = faces[0]
            face_roi = gray[y:y+h, x:x+w]
            
            face_img = Image.fromarray(face_roi)
            count += 1
            filename = f'User.{student_id}.{count}.jpg'
            face_img.save(os.path.join(DATA_DIR, filename))
            saved += 1
    return saved


def train_recognizer():
    image_paths = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.lower().endswith('.jpg')]
    ids = []
    faces = []
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    for image_path in image_paths:
        parts = os.path.basename(image_path).split('.')
        if len(parts) < 3:
            continue
        try:
            student_id = int(parts[1])
        except ValueError:
            continue
        face_img = Image.open(image_path).convert('L')
        face_np = np.array(face_img, 'uint8')
        
        detected_faces = face_cascade.detectMultiScale(face_np, scaleFactor=1.1, minNeighbors=6, minSize=(100, 100))
        if len(detected_faces) > 0:
            detected_faces = sorted(detected_faces, key=lambda f: f[2]*f[3], reverse=True)
            (x, y, w, h) = detected_faces[0]
            faces.append(face_np[y:y+h, x:x+w])
            ids.append(student_id)
        else:
            if face_np.shape[0] < 200 and face_np.shape[1] < 200:
                faces.append(face_np)
                ids.append(student_id)
                
    if len(faces) == 0:
        return False
    recognizer = create_lbph_recognizer()
    recognizer.train(faces, np.array(ids))
    recognizer.write(os.path.join(RECOGNIZER_DIR, 'trainer.yml'))
    return True


def recognize_face(image_data):
    if not os.path.exists(os.path.join(RECOGNIZER_DIR, 'trainer.yml')):
        return None, None
    recognizer = create_lbph_recognizer()
    recognizer.read(os.path.join(RECOGNIZER_DIR, 'trainer.yml'))
    image = parse_base64_image(image_data).convert('RGB')
    frame = np.array(image)
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(100, 100))
    for (x, y, w, h) in faces:
        student_id, confidence = recognizer.predict(gray[y:y + h, x:x + w])
        if confidence < 120:
            return int(student_id), float(confidence)
    return None, None


@app.route('/')
def home():
    if session.get('user_type') == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif session.get('user_type') == 'student':
        return redirect(url_for('student_login'))
    return render_template('index.html')


@app.before_request
def restrict_access():
    allowed_routes = ['home', 'login', 'student_login', 'student_register', 'static', 'test_image', 'get_active_sessions']
    if request.endpoint in allowed_routes or not request.endpoint:
        return
    
    if request.path.startswith('/admin/'):
        if session.get('user_type') != 'admin':
            flash('Admin access required', 'error')
            return redirect(url_for('login'))
    
    if request.path.startswith('/student/'):
        # Some student routes might be public (login, register), handled by allowed_routes
        # But history and attend should be protected
        protected_student_routes = ['student_history', 'student_attend']
        if request.endpoint in protected_student_routes and session.get('user_type') != 'student':
            flash('Student login required', 'error')
            return redirect(url_for('student_login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = query_db('SELECT * FROM admins WHERE username=? AND password=?', (username, password), one=True)
        if admin:
            session.clear()
            session['admin_user'] = username
            session['user_type'] = 'admin'
            return redirect(url_for('admin_dashboard'))
        flash('Invalid username or password', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('home'))


@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    student_count = query_db('SELECT COUNT(*) as count FROM students', one=True)['count']
    session_count = query_db('SELECT COUNT(*) as count FROM sessions', one=True)['count']
    attendance_count = query_db('SELECT COUNT(*) as count FROM attendance', one=True)['count']
    
    # Get detailed attendance records
    attendance_records = query_db('''
        SELECT a.id, a.status, a.timestamp, a.note,
               s.name as student_name, s.roll_no,
               ses.title as session_title, ses.date as session_date, ses.time as session_time,
               sub.name as subject_name, sub.code as subject_code
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.id
        LEFT JOIN sessions ses ON a.session_id = ses.id
        LEFT JOIN subjects sub ON ses.subject_id = sub.id
        ORDER BY a.timestamp DESC
        LIMIT 20
    ''')
    
    low_alerts = []
    threshold = 75
    students = query_db('SELECT * FROM students')
    for student in students:
        total = query_db('SELECT COUNT(*) as count FROM attendance WHERE student_id=?', (student['id'],), one=True)['count']
        present = query_db('SELECT COUNT(*) as count FROM attendance WHERE student_id=? AND status=?', (student['id'], 'Present'), one=True)['count']
        percentage = (present / total * 100) if total > 0 else 0
        if percentage < threshold:
            low_alerts.append({'name': student['name'], 'roll_no': student['roll_no'], 'percentage': round(percentage, 1)})
    
    return render_template('admin_dashboard.html', 
                         students=student_count, 
                         sessions=session_count, 
                         attendance=attendance_count,
                         attendance_records=attendance_records,
                         low_alerts=low_alerts, 
                         threshold=threshold)


@app.route('/admin/students')
def admin_students():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    students = query_db('SELECT * FROM students')
    return render_template('admin_students.html', students=students)


@app.route('/admin/student/<int:student_id>/delete', methods=['POST'])
def delete_student(student_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # 1. Delete attendance records
    execute_db('DELETE FROM attendance WHERE student_id=?', (student_id,))
    
    # 2. Delete face profile from FaceBase.db
    face_conn = sqlite3.connect(FACE_DATABASE_PATH)
    face_cursor = face_conn.cursor()
    face_cursor.execute('DELETE FROM people WHERE id=?', (student_id,))
    face_conn.commit()
    face_conn.close()
    
    # 3. Delete face images from Datasets directory
    try:
        for f in os.listdir(DATA_DIR):
            if f.startswith(f'User.{student_id}.'):
                os.remove(os.path.join(DATA_DIR, f))
    except Exception as e:
        print(f"Error deleting images: {e}")
    
    # 4. Delete student from app.db
    execute_db('DELETE FROM students WHERE id=?', (student_id,))
    
    # 5. Retrain the recognizer (model should be updated without the deleted student)
    train_recognizer()
    
    flash('Student and all related records deleted successfully', 'success')
    return redirect(url_for('admin_students'))


@app.route('/admin/sessions', methods=['GET', 'POST'])
def admin_sessions():
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title')
        subject_name = request.form.get('subject_name')
        subject_code = request.form.get('subject_code')
        date = request.form.get('date')
        
        start_h = int(request.form.get('start_h', 12))
        start_m = int(request.form.get('start_m', 0))
        start_p = request.form.get('start_p', 'AM')
        
        end_h = int(request.form.get('end_h', 12))
        end_m = int(request.form.get('end_m', 0))
        end_p = request.form.get('end_p', 'PM')
        
        def to_24h(h, m, p):
            if p == 'PM' and h < 12: h += 12
            if p == 'AM' and h == 12: h = 0
            return f"{h:02d}:{m:02d}"
            
        time = to_24h(start_h, start_m, start_p)
        end_time = to_24h(end_h, end_m, end_p)
        
        end_date = request.form.get('end_date')
        is_recurring = 1 if request.form.get('is_recurring') else 0
        
        # Convert yyyy-mm-dd to dd-mm-yyyy for internal consistency
        def convert_date(d):
            if not d: return d
            try:
                return datetime.strptime(d, '%Y-%m-%d').strftime('%d-%m-%Y')
            except:
                return d
        
        date = convert_date(date)
        end_date = convert_date(end_date)
        
        if title and subject_name and subject_code and date and time and end_date and end_time:
            subject = query_db('SELECT * FROM subjects WHERE code=?', (subject_code,), one=True)
            if not subject:
                subject_id = execute_db('INSERT INTO subjects(name, code) VALUES(?, ?)', (subject_name, subject_code))
            else:
                subject_id = subject['id']
            execute_db('''INSERT INTO sessions(subject_id, title, date, time, end_date, end_time, is_recurring, active) 
                          VALUES(?, ?, ?, ?, ?, ?, ?, ?)''', 
                       (subject_id, title, date, time, end_date, end_time, is_recurring, 0))
            flash('Session scheduled successfully', 'success')
        else:
            flash('Please fill all session fields', 'error')
    sessions_raw = query_db('SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id')
    sessions = []
    for s in sessions_raw:
        sd = dict(s)
        try:
            sd['time_12h'] = datetime.strptime(s['time'], '%H:%M').strftime('%I:%M %p')
            sd['end_time_12h'] = datetime.strptime(s['end_time'], '%H:%M').strftime('%I:%M %p')
        except:
            sd['time_12h'] = s['time']
            sd['end_time_12h'] = s['end_time']
        sessions.append(sd)
    return render_template('admin_sessions.html', sessions=sessions)


@app.route('/admin/session/<int:session_id>/delete', methods=['POST'])
def delete_session(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Delete attendance records for this session
    execute_db('DELETE FROM attendance WHERE session_id=?', (session_id,))
    
    # Delete the session
    execute_db('DELETE FROM sessions WHERE id=?', (session_id,))
    
    flash('Session and related attendance records deleted successfully', 'success')
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/start')
def start_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    execute_db('UPDATE sessions SET active=1 WHERE id=?', (session_id,))
    return redirect(url_for('attendance_session', session_id=session_id))


@app.route('/admin/session/<int:session_id>/stop')
def stop_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    execute_db('UPDATE sessions SET active=0 WHERE id=?', (session_id,))
    return redirect(url_for('admin_sessions'))


@app.route('/admin/session/<int:session_id>/attendance-data')
def session_attendance_data(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Get all students with their attendance status for this session
    students_with_attendance = query_db('''
        SELECT s.id, s.name, s.roll_no, s.branch, s.semester,
               a.status, a.timestamp,
               CASE WHEN a.status IS NOT NULL THEN 1 ELSE 0 END as has_attendance
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.session_id = ?
        ORDER BY s.roll_no
    ''', (session_id,))
    
    # Debug: Print attendance data
    print(f'DEBUG: Session {session_id} attendance data:')
    for student in students_with_attendance:
        print(f'  Student {student["roll_no"]} ({student["name"]}): {student["status"]} at {student["timestamp"]}')
    
    # Calculate summary stats
    total = len(students_with_attendance)
    present = len([s for s in students_with_attendance if s['status'] == 'Present'])
    absent = total - present
    
    print(f'DEBUG: Summary - Total: {total}, Present: {present}, Absent: {absent}')
    
    return jsonify({
        'students': [dict(s) for s in students_with_attendance],
        'summary': {
            'total': total,
            'present': present,
            'absent': absent
        }
    })


@app.route('/admin/session/<int:session_id>')
def attendance_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    session_info = query_db('SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id WHERE s.id=?', (session_id,), one=True)
    
    # Get all students with their attendance status for this session
    students_with_attendance = query_db('''
        SELECT s.id, s.name, s.roll_no, s.branch, s.semester,
               a.status, a.timestamp,
               CASE WHEN a.status IS NOT NULL THEN 1 ELSE 0 END as has_attendance
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.session_id = ?
        ORDER BY s.roll_no
    ''', (session_id,))
    
    return render_template('attendance_session.html', session_info=session_info, students=students_with_attendance)


@app.route('/admin/session/<int:session_id>/recognize', methods=['POST'])
def session_recognize(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    payload = request.get_json() or {}
    image_data = payload.get('image')
    if not image_data:
        return jsonify({'error': 'No image sent'}), 400
    student_id, confidence = recognize_face(image_data)
    if student_id is None:
        return jsonify({'status': 'not_found'})
    existing = query_db('SELECT 1 FROM attendance WHERE session_id=? AND student_id=?', (session_id, student_id), one=True)
    if not existing:
        execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES(?, ?, ?, ?)', (student_id, session_id, 'Present', datetime.now().isoformat()))
    profile = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    return jsonify({'status': 'present', 'student': {'id': profile['id'], 'name': profile['name'], 'roll_no': profile['roll_no']}, 'confidence': confidence})


@app.route('/admin/session/<int:session_id>/override', methods=['POST'])
def session_override(session_id):
    if not session.get('admin_user'):
        return jsonify({'error': 'Unauthorized'}), 401
    student_id = request.form.get('student_id')
    action = request.form.get('action')
    if not student_id or not action:
        return redirect(url_for('attendance_session', session_id=session_id))
    status = 'Present' if action == 'present' else 'Absent'
    execute_db('DELETE FROM attendance WHERE session_id=? AND student_id=?', (session_id, student_id))
    execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp, note) VALUES(?, ?, ?, ?, ?)', (student_id, session_id, status, datetime.now().isoformat(), 'Manual override'))
    return redirect(url_for('attendance_session', session_id=session_id))


@app.route('/admin/session/<int:session_id>/export')
def export_session(session_id):
    if not session.get('admin_user'):
        return redirect(url_for('login'))
    session_info = query_db('SELECT s.*, subj.name as subject_name, subj.code as subject_code FROM sessions s LEFT JOIN subjects subj ON s.subject_id=subj.id WHERE s.id=?', (session_id,), one=True)
    records = query_db('SELECT a.*, st.name, st.roll_no, st.branch, st.semester FROM attendance a LEFT JOIN students st ON a.student_id=st.id WHERE a.session_id=?', (session_id,))
    csv_file = io.StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(['Student ID', 'Name', 'Roll No', 'Branch', 'Semester', 'Status', 'Timestamp', 'Note'])
    for row in records:
        writer.writerow([row['student_id'], row['name'], row['roll_no'], row['branch'], row['semester'], row['status'], row['timestamp'], row['note']])
    csv_file.seek(0)
    return send_file(io.BytesIO(csv_file.getvalue().encode('utf-8')), mimetype='text/csv', as_attachment=True, download_name=f'session_{session_id}_attendance.csv')


@app.route('/student/register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        data = request.get_json() or {}
        name = data.get('name')
        roll_no = data.get('roll_no')
        branch = data.get('branch')
        semester = data.get('semester')
        password = data.get('password')
        gender = data.get('gender', 'other')
        images = data.get('images', [])
        if not name or not roll_no or not branch or not semester or not password or not images:
            return jsonify({'error': 'Missing fields'}), 400

        # Check if roll_no already exists
        existing_roll = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
        if existing_roll:
            return jsonify({'error': 'Roll number already registered'}), 400

        # Check if a face is actually present in the provided images
        has_face = False
        face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
        for img_data in images:
            img = parse_base64_image(img_data)
            frame = np.array(img.convert('RGB'))
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            if len(face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(100, 100))) > 0:
                has_face = True
                break
                
        if not has_face:
            return jsonify({'error': 'No face detected in the images! Please look directly at the camera in a well-lit area.'}), 400

        # Check if this face already exists
        if os.path.exists(os.path.join(RECOGNIZER_DIR, 'trainer.yml')):
            existing_student_id = None
            for img_data in images[:5]:  # Check the first 5 images
                found_id, confidence = recognize_face(img_data)
                if found_id is not None and confidence < 95: # Use a strict confidence threshold for duplicate check
                    existing_student_id = found_id
                    break
            
            if existing_student_id is not None:
                existing_student = query_db('SELECT * FROM students WHERE id=?', (existing_student_id,), one=True)
                if existing_student:
                    return jsonify({'error': f'Face already registered to {existing_student["name"]} ({existing_student["roll_no"]})'}), 400

        student_id = execute_db('INSERT INTO students(name, roll_no, branch, semester, password) VALUES(?, ?, ?, ?, ?)', (name, roll_no, branch, semester, password))
        save_face_profile(student_id, name, gender, branch)
        saved = save_face_images(student_id, images)
        train_recognizer()
        execute_db('UPDATE students SET photo_count=? WHERE id=?', (saved, student_id))
        return jsonify({'status': 'ok', 'student_id': student_id})
    return render_template('student_register.html')


@app.route('/student/login', methods=['GET', 'POST'])
def student_login():
    if session.get('user_type') == 'student':
        return render_template('student_login.html', logged_in=True)

    if request.method == 'POST':
        data = request.get_json() or {}
        roll_no = data.get('roll_no')
        password = data.get('password')
        
        if not roll_no or not password:
            return jsonify({'status': 'error', 'message': 'Roll number and password required'}), 400
        
        student = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
        if not student:
            return jsonify({'status': 'error', 'message': 'Student not found. Please register first.'}), 404
        
        if student['password'] != password:
            return jsonify({'status': 'error', 'message': 'Incorrect password'}), 401
        
        session.clear()
        session['student_id'] = student['id']
        session['student_name'] = student['name']
        session['user_type'] = 'student'
        
        return jsonify({'status': 'ok', 'student': dict(student)})
    
    return render_template('student_login.html', logged_in=False)


@app.route('/student/search')
def student_search():
    roll_no = request.args.get('roll_no', '').strip()
    if not roll_no:
        return jsonify({'status': 'error', 'message': 'Roll number required'}), 400
    student = query_db('SELECT * FROM students WHERE roll_no=?', (roll_no,), one=True)
    if student:
        return jsonify({'status': 'ok', 'student': dict(student)})
    return jsonify({'status': 'error', 'message': 'Student not found'}), 404


@app.route('/get-active-sessions')
def get_active_sessions():
    now = datetime.now()
    current_date = now.strftime('%d-%m-%Y')
    current_time = now.strftime('%H:%M')
    
    sessions = query_db('''
        SELECT s.*, sub.name as subject_name, sub.code as subject_code 
        FROM sessions s 
        LEFT JOIN subjects sub ON s.subject_id = sub.id 
        ORDER BY s.date DESC, s.time DESC
    ''')
    
    active_sessions = []
    for s in sessions:
        is_active = False
        if s['active'] == 1:
            is_active = True
        elif s['date'] and s['time'] and s['end_date'] and s['end_time']:
            try:
                start_dt = datetime.strptime(f"{s['date']} {s['time']}", '%d-%m-%Y %H:%M')
                end_dt = datetime.strptime(f"{s['end_date']} {s['end_time']}", '%d-%m-%Y %H:%M')
                
                if s['is_recurring'] == 1:
                    # Recurring every day: check if current date is in range and current time is in range
                    curr_date_dt = datetime.strptime(current_date, '%d-%m-%Y')
                    start_date_dt = datetime.strptime(s['date'], '%d-%m-%Y')
                    end_date_dt = datetime.strptime(s['end_date'], '%d-%m-%Y')
                    
                    if start_date_dt <= curr_date_dt <= end_date_dt:
                        # Date is in range, now check time
                        if s['time'] <= current_time <= s['end_time']:
                            is_active = True
                else:
                    # Not recurring: just check if now is between start and end
                    if start_dt <= now <= end_dt:
                        is_active = True
            except Exception as e:
                print(f"Error checking session bounds for ID {s['id']}: {e}")
        
        if is_active:
            # Add formatted info for the UI
            session_dict = dict(s)
            session_dict['is_auto_active'] = (s['active'] == 0)
            try:
                session_dict['time_12h'] = datetime.strptime(s['time'], '%H:%M').strftime('%I:%M %p')
                session_dict['end_time_12h'] = datetime.strptime(s['end_time'], '%H:%M').strftime('%I:%M %p')
            except:
                session_dict['time_12h'] = s['time']
                session_dict['end_time_12h'] = s['end_time']
            active_sessions.append(session_dict)
            
    return jsonify({'sessions': active_sessions})


@app.route('/student/attend')
def student_attend():
    session_id = request.args.get('session_id')
    student_id = session.get('student_id')
    
    if not student_id:
        return redirect(url_for('student_login'))
    
    try:
        session_id = int(session_id) if session_id else None
    except:
        session_id = None
    
    if not session_id:
        flash('Please select a session first', 'warning')
        return redirect(url_for('student_login'))
    
    session_info = query_db('SELECT s.*, sub.name as subject_name, sub.code as subject_code FROM sessions s LEFT JOIN subjects sub ON s.subject_id = sub.id WHERE s.id=?', (session_id,), one=True)
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    
    if not session_info or not student:
        flash('Session or student record not found', 'error')
        return redirect(url_for('student_login'))
    
    return render_template('student_attend.html', session_info=dict(session_info), student=dict(student))


@app.route('/student/test-image', methods=['POST'])
def test_image():
    """Test endpoint to check if image is being received properly"""
    try:
        data = request.get_json() or {}
        image = data.get('image')
        
        if not image:
            return jsonify({'status': 'error', 'message': 'No image received'}), 400
        
        # Process image data
        if not image.startswith('data:image/'):
            return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
        
        img_data = image.split(',')[1] if ',' in image else image
        img_bytes = base64.b64decode(img_data)
        img = Image.open(io.BytesIO(img_bytes))
        
        # Convert to numpy array and grayscale
        nparr = np.array(img)
        if len(nparr.shape) == 3 and nparr.shape[2] == 3:
            gray = cv2.cvtColor(nparr, cv2.COLOR_RGB2GRAY)
        elif len(nparr.shape) == 2:
            gray = nparr
        else:
            return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
        
        # Save the image for inspection
        test_img_path = os.path.join(BASE_DIR, 'test_capture.jpg')
        cv2.imwrite(test_img_path, gray)
        
        return jsonify({
            'status': 'success', 
            'message': f'Image received and saved to {test_img_path}',
            'shape': gray.shape,
            'size': len(img_bytes)
        })
        
    except Exception as e:
        print(f'Test image error: {e}')
        return jsonify({'status': 'error', 'message': f'Error: {str(e)}'}), 500


@app.route('/student/attend/mark', methods=['POST'])
def student_mark_attendance():
    try:
        data = request.get_json() or {}
        session_id = data.get('session_id')
        student_id = data.get('student_id')
        image = data.get('image')
        
        # Convert to integers with validation
        try:
            session_id = int(session_id) if session_id else None
            student_id = int(student_id) if student_id else None
        except (ValueError, TypeError):
            return jsonify({'status': 'error', 'message': 'Invalid session_id or student_id'}), 400
        
        if not session_id or not student_id or not image:
            return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        
        # Check if session is active
        session = query_db('SELECT * FROM sessions WHERE id=?', (session_id,), one=True)
        if not session or not session['active']:
            return jsonify({'status': 'error', 'message': 'Session not active'}), 400
        
        # Check if already marked
        existing = query_db('SELECT * FROM attendance WHERE student_id=? AND session_id=?', (student_id, session_id), one=True)
        if existing:
            return jsonify({'status': 'already_marked', 'message': 'Already marked attendance'}), 200
        
        # Try to recognize face
        try:
            # Process image data
            if not image.startswith('data:image/'):
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
            
            img_data = image.split(',')[1] if ',' in image else image
            img_bytes = base64.b64decode(img_data)
            img = Image.open(io.BytesIO(img_bytes))
            
            # Convert to numpy array and grayscale
            nparr = np.array(img)
            if len(nparr.shape) == 3 and nparr.shape[2] == 3:
                gray = cv2.cvtColor(nparr, cv2.COLOR_RGB2GRAY)
            elif len(nparr.shape) == 2:
                gray = nparr
            else:
                return jsonify({'status': 'error', 'message': 'Invalid image format'}), 400
            
            # Detect faces
            if not os.path.exists(CASCADE_PATH):
                return jsonify({'status': 'error', 'message': 'Face cascade file not found'}), 500
            
            # Debug: Save the processed image to check
            debug_img_path = os.path.join(BASE_DIR, 'debug_face.jpg')
            cv2.imwrite(debug_img_path, gray)
            print(f'Debug: Saved processed image to {debug_img_path}, shape: {gray.shape}')
            
            cascade = cv2.CascadeClassifier(CASCADE_PATH)
            # Use strict face detection to prevent false positives on backgrounds
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(100, 100))
            print(f'Debug: First detection found {len(faces)} faces')
            
            if len(faces) == 0:
                # One fallback with slightly different scale, but still strict
                faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))
                print(f'Debug: Second detection found {len(faces)} faces')
            
            if len(faces) == 0:
                return jsonify({'status': 'error', 'message': 'No clear face detected! Please look directly at the camera in a well-lit area.'}), 200
            
            # Try to recognize face
            trainer_path = os.path.join(RECOGNIZER_DIR, 'trainer.yml')
            if not os.path.exists(trainer_path):
                return jsonify({'status': 'error', 'message': 'Face recognition model not trained. Please contact administrator.'}), 200
            
            recognizer = cv2.face.LBPHFaceRecognizer_create()
            recognizer.read(trainer_path)
            
            # Sort faces by size (area) in descending order to get the largest/closest face
            faces = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)
            (x, y, w, h) = faces[0]
            face_roi = gray[y:y+h, x:x+w]
            
            # Predict face
            face_id, confidence = recognizer.predict(face_roi)
            
            print(f'DEBUG: Recognized ID: {face_id}, Confidence: {confidence:.2f}, Expected Student ID: {student_id}')

            # LBPH Confidence: lower is better. < 50 is excellent, < 100 is good, < 120 is acceptable.
            # Enforce strict ID matching and a stricter confidence threshold (100)
            if confidence < 100:
                if int(face_id) == student_id:
                    status = 'Present'
                    note = f'Face recognized (Conf: {confidence:.1f})'
                    # Mark attendance
                    attendance_id = execute_db('INSERT INTO attendance(student_id, session_id, status, timestamp, note) VALUES(?, ?, ?, ?, ?)', 
                              (student_id, session_id, status, datetime.now().isoformat(), note))
                    # Return success with face match details
                    profile = query_db('SELECT name FROM students WHERE id=?', (student_id,), one=True)
                    match_msg = f'Face detected and matched to {profile["name"]} (Confidence score: {100 - min(confidence, 100):.1f}/100)'
                    return jsonify({'status': 'present', 'message': match_msg, 'confidence': 100 - min(confidence, 100)})
                else:
                    return jsonify({'status': 'error', 'message': 'Face does not match your registered profile! Attendance denied.'}), 200
            else:
                return jsonify({'status': 'error', 'message': f'Face not recognized with high enough confidence (Score: {100 - min(confidence, 100):.1f}/100). Please improve lighting and try again.'}), 200
                
        except Exception as e:
            print(f'Face recognition error: {e}')
            return jsonify({'status': 'error', 'message': f'Face recognition failed: {str(e)}'}), 200
            
    except Exception as e:
        print(f'General error: {e}')
        return jsonify({'status': 'error', 'message': f'Server error: {str(e)}'}), 200


@app.route('/student/history')
def student_history():
    student_id = session.get('student_id')
    if not student_id:
        return redirect(url_for('student_login'))
        
    student = query_db('SELECT * FROM students WHERE id=?', (student_id,), one=True)
    if not student:
        flash('Student record not found', 'error')
        return redirect(url_for('home'))
        
    attendance = query_db('SELECT a.*, s.title, s.date, s.time FROM attendance a LEFT JOIN sessions s ON a.session_id=s.id WHERE a.student_id=? ORDER BY a.timestamp DESC', (student_id,))
    total = len(attendance)
    present = len([r for r in attendance if r['status'] == 'Present'])
    percentage = round((present / total * 100) if total > 0 else 0, 1)
    
    return render_template('student_history.html', student=student, attendance=attendance, percentage=percentage)


if __name__ == '__main__':
    init_databases()
    app.run(host='0.0.0.0', port=5000, debug=True)
