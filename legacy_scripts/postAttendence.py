import cv2,os
import numpy as np
from PIL import Image
import pickle
import sqlite3
from datetime import date,datetime
from post_date_time import post_date_time

def create_lbph_recognizer():
    if hasattr(cv2, 'face'):
        face = cv2.face
        if hasattr(face, 'LBPHFaceRecognizer_create'):
            return face.LBPHFaceRecognizer_create()
        if hasattr(face, 'createLBPHFaceRecognizer'):
            return face.createLBPHFaceRecognizer()
    raise AttributeError('OpenCV LBPH face recognizer is unavailable in this environment. Install opencv-contrib-python for the same Python interpreter.')

def getprofileId(id):
    conn=sqlite3.connect('FaceBase.db')
    cmd='select * from people where id='+str(id)
    cursor=conn.execute(cmd)
    profile=None
    for row in cursor:
        profile=row
    conn.close()
    return profile

def postatt(id,prd):
    conn=sqlite3.connect('attendenceBase.db')
    cursor=conn.execute('SELECT 1 FROM attendence WHERE Id=?',(id,))
    if cursor.fetchone() is None:
        conn.execute('INSERT INTO attendence(Id) VALUES(?)',(id,))
    conn.execute(f'UPDATE attendence SET {prd}=? WHERE Id=?',('Present',id))
    conn.commit()
    conn.close()


faceDetect=cv2.CascadeClassifier('haarcascade_frontalface_default.xml');
cam=cv2.VideoCapture(0);
path='Datasets'

rec = create_lbph_recognizer()
rec.read('recognizer\\trainer.yml')
id=0
font=cv2.FONT_HERSHEY_SIMPLEX
prd=input("\nEnter the period(p) with number to post attendence .\nEx : p1\n")
while True:
    ret,img=cam.read()
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    faces=faceDetect.detectMultiScale(gray,scaleFactor=1.2,minNeighbors=5,minSize=(100,100),flags=cv2.CASCADE_SCALE_IMAGE)
    for(x,y,w,h) in faces:
        cv2.rectangle(img,(x,y),(x+w,y+h),(0,0,255),2)
        id,conf=rec.predict(gray[y:y+h,x:x+w])
        profile=getprofileId(id)
        postatt(id,prd)
        post_date_time(id,prd)
        if profile != None:
            cv2.putText(img,'Hiii '+str(profile[1]),(x,y+h+30),cv2.FONT_HERSHEY_TRIPLEX,1,(255,144,30),2)
            cv2.putText(img,'Your Attendence is Posted ',(x,y+h+60),cv2.FONT_HERSHEY_COMPLEX_SMALL,1,(30,144,255),2)
            #cv2.putText(img,'At Date & Time :',d_t[0],(x,y+h+60),cv2.FONT_HERSHEY_COMPLEX_SMALL,1,(0,69255),2)
    cv2.imshow('Face',img)
    if cv2.waitKey(20)==ord('q') :
        break
cam.release()
cv2.destroyAllWindows()
