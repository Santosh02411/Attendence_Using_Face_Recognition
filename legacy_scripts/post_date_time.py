from datetime import date,datetime
import sqlite3
def post_date_time(id,prd):
    conn=sqlite3.connect('datetime.db')
    if prd not in ('p1','p2','p3','p4','p5','p6','p7'):
        raise ValueError('Invalid period')
    cursor=conn.execute('SELECT 1 FROM date_time WHERE Id=?',(id,))
    if cursor.fetchone() is None:
        conn.execute('INSERT INTO date_time(Id) VALUES(?)',(id,))
    conn.execute(f'UPDATE date_time SET {prd}=? WHERE Id=?',(datetime.now(),id))
    conn.commit()
    conn.close()

#post_date_time(11,'p2')
#conn.execute("CREATE TABLE date_time (id integer primary key, p1 timestamp, p2 timestamp, p3 timestamp, p4 timestamp, p5 timestamp, p6 timestamp, p7 timestamp)")

#conn.execute('insert into date_time('+str(prd)+') values(?)'+' where id=?',(str(prd),datetime.now(),id))
#return  profile
#print(profile[0])

#conn.execute('UPDATE date_time SET '+str(prd)+'=?'+' WHERE Id=?',(datetime.now(),str(id)))


