import unittest,tempfile,os,sys
from datetime import datetime,timedelta,timezone
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import app
class Rules(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();app.DB=self.tmp.name+'/test.db';app.init();self.c=app.connect()
  self.c.execute("INSERT INTO users(id,name,email,mobile,password) VALUES(1,'Test','t@a','123','hash')")
  self.c.execute("INSERT INTO drivers VALUES(1,'Driver','123',1)")
  self.c.execute("INSERT INTO tankers VALUES(1,'TEST',5000,'Central',750,1,1)");self.c.commit();self.u=self.c.execute('SELECT * FROM users').fetchone();self.s=(datetime.now(timezone.utc)+timedelta(days=3)).strftime('%Y-%m-%dT09:00')
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def book(self,s=None):
  result=app.book(self.c,self.u,{'tanker_id':1,'scheduled':s or self.s});self.c.commit();return result
 def test_duplicate_slot(self):
  self.book()
  with self.assertRaisesRegex(ValueError,'already'):self.book()
 def test_limit(self):
  self.book();self.book(self.s.replace('09:00','13:00'))
  with self.assertRaisesRegex(ValueError,'limit'):self.book(self.s.replace('09:00','17:00'))
 def test_cancel_reopens(self):
  self.book();app.cancel(self.c,self.u,1);self.c.commit();self.book()
 def test_late_cancel(self):
  self.book();self.c.execute('UPDATE bookings SET scheduled=?',((datetime.now(timezone.utc)+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),));self.c.commit()
  with self.assertRaisesRegex(ValueError,'24 hours'):app.cancel(self.c,self.u,1)
 def test_ownership(self):
  self.book()
  with self.assertRaisesRegex(ValueError,'not found'):app.cancel(self.c,{'id':2},1)
 def test_disabled(self):
  self.c.execute('UPDATE tankers SET active=0');self.c.commit()
  with self.assertRaisesRegex(ValueError,'unavailable'):self.book()
 def test_password(self):
  h=app.password('long password');self.assertNotIn('long password',h);self.assertEqual(h,app.password('long password',h.split(':')[0]))
if __name__=='__main__':unittest.main()
