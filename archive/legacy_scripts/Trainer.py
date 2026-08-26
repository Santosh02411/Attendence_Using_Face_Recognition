import os
import numpy as np
import cv2

from PIL import Image

# Some how the method cv2.createLBPHFaceRecognizer() is not working.

def create_lbph_recognizer():
    if hasattr(cv2, 'face'):
        face = cv2.face
        if hasattr(face, 'LBPHFaceRecognizer_create'):
            return face.LBPHFaceRecognizer_create()
        if hasattr(face, 'createLBPHFaceRecognizer'):
            return face.createLBPHFaceRecognizer()
    raise AttributeError('OpenCV LBPH face recognizer is unavailable in this environment. Install opencv-contrib-python for the same Python interpreter.')

recognizer = create_lbph_recognizer()
path='Datasets'

def getImagewithId(path):
    imagePaths=[os.path.join(path,f) for f in os.listdir(path)]
    faces=[]
    Ids=[]
    for imagePath in imagePaths:
        faceImg=Image.open(imagePath).convert('L')
        faceNp=np.array(faceImg,'uint8')
        Id=int(os.path.split(imagePath)[-1].split('.')[1])
        faces.append(faceNp)
        print(Id)
        Ids.append(Id)
        cv2.imshow('Training',faceNp)
        cv2.waitKey(10)
    return np.array(Ids),faces


Ids,faces=getImagewithId(path)

recognizer.train(faces,Ids)
recognizer.save('recognizer/trainer.yml')
cv2.destroyAllWindows()
