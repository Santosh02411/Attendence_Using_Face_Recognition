# Attendance Using Face Recognition

A comprehensive attendance management system powered by face recognition technology. This web application provides an automated, secure, and efficient way to track student attendance using facial recognition algorithms.

## 🚀 Features

- **Face Recognition Attendance**: Automated attendance marking using facial recognition
- **Admin Dashboard**: Complete control over sessions, students, and attendance records
- **Student Portal**: Self-service registration and attendance viewing
- **Session Management**: Create and manage attendance sessions for different subjects
- **Real-time Recognition**: Live face detection and recognition during attendance sessions
- **Export Functionality**: Export attendance records as CSV files
- **Low Attendance Alerts**: Automatic alerts for students with low attendance percentages
- **Multi-camera Support**: Compatible with web cameras for face capture

## 🛠️ Technology Stack

- **Backend**: Flask (Python Web Framework)
- **Face Recognition**: OpenCV with LBPH (Local Binary Patterns Histograms) algorithm
- **Database**: SQLite (for both application data and face recognition data)
- **Frontend**: HTML5, CSS3, JavaScript, Bootstrap
- **Image Processing**: PIL (Python Imaging Library)
- **Face Detection**: Haar Cascade Classifiers

## 📋 Prerequisites

- Python 3.7 or higher
- Webcam or camera device
- Git (for cloning the repository)

## 🚀 Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-username/Attendance_Using_Face_Recognition.git
   cd Attendance_Using_Face_Recognition
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv venv
   
   # On Windows
   venv\Scripts\activate
   
   # On macOS/Linux
   source venv/bin/activate
   ```

3. **Install required dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application**
   ```bash
   python app.py
   ```

5. **Access the application**
   - Open your web browser and navigate to `http://localhost:5000`
   - Default admin credentials:
     - Username: `admin`
     - Password: `admin123`

## 📁 Project Structure

```
Attendance_Using_Face_Recognition/
├── app.py                          # Main Flask application
├── requirements.txt                # Python dependencies
├── haarcascade_frontalface_default.xml  # Face detection cascade file
├── Datasets/                       # Student face images directory
├── recognizer/                     # Trained face recognition models
│   └── trainer.yml                # Trained LBPH model
├── templates/                      # HTML templates
│   ├── base.html                 # Base template
│   ├── login.html                # Admin login page
│   ├── admin_dashboard.html      # Admin dashboard
│   ├── admin_sessions.html        # Session management
│   ├── attendance_session.html   # Live attendance session
│   ├── student_register.html     # Student registration
│   ├── student_login.html        # Student login
│   ├── student_attend.html       # Student attendance marking
│   └── student_history.html      # Attendance history
├── static/                        # Static assets (CSS, JS, images)
├── Basic Programs/                # Additional utility scripts
├── Detector.py                   # Face detection script
├── Trainer.py                    # Face recognition training script
├── Dataset_Creator.py           # Dataset creation utility
├── postAttendence.py            # Attendance posting utility
└── post_date_time.py            # Date/time utilities
```

## 🎯 How It Works

### 1. Student Registration
- Students register with their details (name, roll number, branch, semester)
- Multiple face images are captured for training
- Face profiles are stored in the database
- Recognition model is automatically trained

### 2. Admin Session Management
- Admins create attendance sessions for specific subjects
- Sessions can be scheduled with date and time
- Sessions can be activated/deactivated as needed

### 3. Attendance Marking
- **Admin Mode**: Live face recognition during active sessions
- **Student Mode**: Self-service attendance marking with face verification
- Real-time face detection and recognition
- Automatic attendance recording with timestamps

### 4. Face Recognition Process
- Uses LBPH (Local Binary Patterns Histograms) algorithm
- Haar cascade for face detection
- Confidence threshold for recognition accuracy
- Fallback mechanisms for detection failures

## 🔧 Configuration

### Database Setup
The application automatically creates and initializes SQLite databases:
- `app.db`: Main application data (students, sessions, attendance)
- `FaceBase.db`: Face recognition profiles

### Face Recognition Settings
Key parameters in `app.py`:
- **Confidence Threshold**: 120 (adjustable based on accuracy needs)
- **Face Detection Parameters**: scaleFactor=1.2, minNeighbors=5
- **Minimum Face Size**: 100x100 pixels

## 📊 Database Schema

### Main Tables
- **admins**: Administrator credentials
- **students**: Student information and credentials
- **subjects**: Subject/course information
- **sessions**: Attendance session details
- **attendance**: Attendance records with timestamps

### Face Recognition Tables
- **people**: Face profile information linked to student IDs

## 🎨 User Interface

### Admin Features
- Dashboard with attendance statistics
- Student management
- Session creation and management
- Live attendance monitoring
- Attendance record export
- Low attendance alerts

### Student Features
- Self-registration with face capture
- Login with credentials
- View attendance history
- Mark attendance for active sessions
- Attendance percentage calculation

## 🔒 Security Features

- Admin authentication with session management
- Student credential verification
- Face recognition validation
- Secure image handling and storage
- SQL injection protection
- Session-based authentication

## 🚨 Troubleshooting

### Common Issues

1. **Face Recognition Not Working**
   - Ensure `opencv-contrib-python` is installed
   - Check if webcam is properly connected
   - Verify training data exists in `Datasets/` folder

2. **Camera Not Detected**
   - Check camera permissions
   - Ensure no other application is using the camera
   - Try restarting the application

3. **Database Errors**
   - Delete existing `.db` files and restart the application
   - Ensure write permissions in the project directory

4. **Face Detection Issues**
   - Ensure proper lighting conditions
   - Position face clearly in front of camera
   - Check if `haarcascade_frontalface_default.xml` exists

### Debug Mode
The application includes debug features:
- Debug images saved as `debug_face.jpg`
- Console output for face detection results
- Confidence scores displayed in logs

## 🔄 Advanced Usage

### Training the Recognition Model
```bash
# Run the trainer script
python Trainer.py
```

### Face Detection Testing
```bash
# Test face detection with camera
python Detector.py
```

### Creating Custom Datasets
```bash
# Create datasets for new students
python Dataset_Creator.py
```

## 📈 Performance Optimization

- **Image Resolution**: Optimize camera resolution for faster processing
- **Confidence Thresholds**: Adjust based on environment conditions
- **Database Indexing**: Automatic indexing for faster queries
- **Caching**: Session data caching for improved performance

## 🔮 Future Enhancements

- [ ] Multi-language support
- [ ] Mobile application
- [ ] Cloud storage integration
- [ ] Advanced analytics dashboard
- [ ] SMS/email notifications
- [ ] Biometric integration (fingerprint, iris)
- [ ] AI-powered attendance predictions

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 👥 Authors

- **Santosh** - *Initial development* - [YourGitHubUsername](https://github.com/your-username)

## 🙏 Acknowledgments

- OpenCV community for face recognition algorithms
- Flask framework for web development
- Bootstrap for UI components
- All contributors and users of this project

## 📞 Support

For support and queries:
- Create an issue in the GitHub repository
- Email: your-email@example.com
- Documentation: [Link to documentation]

---

**Note**: This application is designed for educational and institutional use. Ensure compliance with privacy laws and regulations when implementing face recognition systems.