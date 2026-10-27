import os, sqlite3, json, secrets, hashlib, hmac, time, ipaddress, argparse
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs
from pathlib import Path

ROOT=Path(__file__).parent
DB=os.environ.get('WTMS_DB',str(ROOT/'wtms.db'))
def connect():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def password(p,s=None):
 s=s or secrets.token_hex(16); return s+':'+hashlib.scrypt(p.encode(),salt=s.encode(),n=16384,r=8,p=1).hex()
def init():
 with connect() as c:
  c.executescript('''
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,email TEXT UNIQUE,mobile TEXT UNIQUE,password TEXT,role TEXT DEFAULT 'citizen',kind TEXT DEFAULT 'residential',failures INTEGER DEFAULT 0,locked REAL DEFAULT 0);
  CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),csrf TEXT,expires REAL);
  CREATE TABLE IF NOT EXISTS drivers(id INTEGER PRIMARY KEY,name TEXT,mobile TEXT,active INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS tankers(id INTEGER PRIMARY KEY,plate TEXT UNIQUE,capacity INTEGER,zone TEXT,price INTEGER,driver_id INTEGER REFERENCES drivers(id),active INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),tanker_id INTEGER REFERENCES tankers(id),scheduled TEXT,status TEXT DEFAULT 'Pending',price INTEGER,driver_id INTEGER REFERENCES drivers(id));
  CREATE UNIQUE INDEX IF NOT EXISTS unique_slot ON bookings(tanker_id,scheduled) WHERE status != 'Cancelled';
  CREATE TABLE IF NOT EXISTS settings(kind TEXT PRIMARY KEY,booking_limit INTEGER);
  INSERT OR IGNORE INTO settings VALUES('residential',2),('commercial',5);
  CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor INTEGER,action TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS recovery(token TEXT PRIMARY KEY,user_id INTEGER,expires REAL);
  ''')
def require(ok,msg):
 if not ok: raise ValueError(msg)
def date(s):
 try: return datetime.strptime(s,'%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
 except: raise ValueError('Choose a valid delivery date and time.')
def future(s): require(date(s).timestamp()>time.time(),'Delivery must be in the future.')
def book(c,u,d):
 future(d['scheduled']); require(date(d['scheduled']).hour in (9,13,17) and date(d['scheduled']).minute==0,'Invalid delivery slot.')
 c.execute('BEGIN IMMEDIATE')
 limit=c.execute('SELECT booking_limit FROM settings WHERE kind=?',(u['kind'],)).fetchone()[0]
 count=c.execute("SELECT count(*) FROM bookings WHERE user_id=? AND status IN ('Pending','Dispatched')",(u['id'],)).fetchone()[0]
 require(count<limit,'Active booking limit reached.')
 t=c.execute('SELECT t.* FROM tankers t JOIN drivers d ON d.id=t.driver_id WHERE t.id=? AND t.active=1 AND d.active=1',(d['tanker_id'],)).fetchone(); require(t,'Tanker unavailable.')
 try: c.execute('INSERT INTO bookings(user_id,tanker_id,scheduled,price,driver_id) VALUES(?,?,?,?,?)',(u['id'],t['id'],d['scheduled'],t['price'],t['driver_id']))
 except sqlite3.IntegrityError: raise ValueError('This slot has already been booked.')
 return {'message':'Booking confirmed.'}
def cancel(c,u,bid):
 b=c.execute('SELECT * FROM bookings WHERE id=? AND user_id=?',(bid,u['id'])).fetchone(); require(b,'Booking not found.')
 require(b['status']=='Pending','Only pending bookings can be cancelled.'); require(date(b['scheduled']).timestamp()-time.time()>=86400,'Cancellation requires at least 24 hours notice.')
 c.execute("UPDATE bookings SET status='Cancelled' WHERE id=?",(bid,)); return {'message':'Booking cancelled.'}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def respond(self,data,status=200,cookie=None):
  body=json.dumps(data).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers(); self.wfile.write(body)
 def do_GET(self): self.handle_request()
 def do_POST(self): self.handle_request()
 def handle_request(self):
  path=urlparse(self.path).path
  if not path.startswith('/api/'):
   files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
   if path not in files:self.send_error(404);return
   data=(ROOT/'static'/files[path]).read_bytes();self.send_response(200);self.send_header('Content-Type',{'/':'text/html','/app.js':'text/javascript','/style.css':'text/css'}[path]);self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(data);return
  c=connect()
  try:
   d={k:v[0] for k,v in parse_qs(urlparse(self.path).query).items()}
   if self.command=='POST':
    require(int(self.headers.get('Content-Length','0'))<=16384,'Request too large.');require(self.headers.get('Content-Type','').startswith('application/json'),'JSON required.');d=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
   cookies=SimpleCookie(self.headers.get('Cookie',''));token=cookies['session'].value if 'session' in cookies else ''
   session=c.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(token,time.time())).fetchone()
   u=c.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone() if session else None
   if self.command=='POST' and path not in ['/api/login','/api/register','/api/reset']:
    require(u,'Please sign in.');require(hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']),'Session verification failed.')
   if path.startswith('/api/staff'):
    require(u and u['role'] in ('admin','dispatcher'),'Staff access required.')
    nets=os.environ.get('WTMS_STAFF_NETWORKS','127.0.0.0/8,::1/128').split(',');require(any(ipaddress.ip_address(self.client_address[0]) in ipaddress.ip_network(n) for n in nets),'Municipal network or VPN required.')
   result={};cookie=None
   if path=='/api/register' and self.command=='POST':
    email=d['email'].strip().lower();mobile=''.join(x for x in d['mobile'] if x.isdigit());require('@' in email and len(email)<255,'Valid email required.');require(8<=len(mobile)<=15,'Valid mobile required.');require(len(d['password'])>=10,'Password must contain at least 10 characters.');require(d['kind'] in ('residential','commercial'),'Invalid account type.');require(d['name'].strip(),'Name required.')
    try:c.execute('INSERT INTO users(name,email,mobile,password,kind) VALUES(?,?,?,?,?)',(d['name'].strip(),email,mobile,password(d['password']),d['kind']))
    except sqlite3.IntegrityError:raise ValueError('Email or mobile already registered.')
    result={'message':'Account created. Please sign in.'}
   elif path=='/api/login' and self.command=='POST':
    row=c.execute('SELECT * FROM users WHERE email=?',(d['email'].strip().lower(),)).fetchone()
    if not row or row['locked']>time.time() or not hmac.compare_digest(password(d['password'],row['password'].split(':')[0]),row['password']):
     if row:c.execute('UPDATE users SET failures=failures+1,locked=? WHERE id=?',(time.time()+900 if row['failures']>=2 else 0,row['id']));c.commit()
     raise ValueError('Invalid credentials or account temporarily locked. After three failures, wait 15 minutes or contact the administrator.')
    c.execute('UPDATE users SET failures=0,locked=0 WHERE id=?',(row['id'],));token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(token,row['id'],csrf,time.time()+28800));cookie='session='+token+'; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if os.environ.get('WTMS_SECURE_COOKIE')=='1' else '');result={'message':'Signed in.'}
   elif path=='/api/reset' and self.command=='POST':
    r=c.execute('SELECT * FROM recovery WHERE token=? AND expires>?',(hashlib.sha256(d['token'].encode()).hexdigest(),time.time())).fetchone();require(r,'Invalid or expired recovery token.');require(len(d['password'])>=10,'Use at least 10 characters.');c.execute('UPDATE users SET password=?,failures=0,locked=0 WHERE id=?',(password(d['password']),r['user_id']));c.execute('DELETE FROM recovery WHERE user_id=?',(r['user_id'],));c.execute('DELETE FROM sessions WHERE user_id=?',(r['user_id'],));result={'message':'Password reset. Sign in again.'}
   elif path=='/api/logout' and self.command=='POST':c.execute('DELETE FROM sessions WHERE token=?',(token,));result={'message':'Signed out.'}
   elif path=='/api/me':result={'user':{k:u[k] for k in ('id','name','role','kind')} if u else None,'csrf':session['csrf'] if session else ''}
   elif path=='/api/search':
    future(d['date']+'T17:00'); rows=c.execute('SELECT t.*,d.name driver FROM tankers t JOIN drivers d ON t.driver_id=d.id WHERE t.active=1 AND d.active=1 AND t.zone=? AND t.capacity=?',(d['zone'],int(d['capacity']))).fetchall();result=[]
    for t in rows:
     for hour in ('09:00','13:00','17:00'):
      s=d['date']+'T'+hour
      if date(s).timestamp()>time.time() and not c.execute("SELECT 1 FROM bookings WHERE tanker_id=? AND scheduled=? AND status!='Cancelled'",(t['id'],s)).fetchone():result.append(dict(t,scheduled=s))
   elif path=='/api/book' and self.command=='POST':require(u['role']=='citizen','Citizen account required.');result=book(c,u,d)
   elif path=='/api/cancel' and self.command=='POST':result=cancel(c,u,d['id'])
   elif path=='/api/bookings':
    require(u,'Please sign in.');result=[dict(r) for r in c.execute('SELECT b.*,t.plate,t.capacity,t.zone,d.name driver FROM bookings b JOIN tankers t ON b.tanker_id=t.id LEFT JOIN drivers d ON b.driver_id=d.id WHERE b.user_id=? ORDER BY b.scheduled DESC',(u['id'],))]
   elif path=='/api/staff/data':result={table:[dict(r) for r in c.execute('SELECT * FROM '+table)] for table in ('tankers','drivers','settings','audit')};result['bookings']=[dict(r) for r in c.execute('SELECT b.*,u.name,u.mobile,t.plate FROM bookings b JOIN users u ON u.id=b.user_id JOIN tankers t ON t.id=b.tanker_id ORDER BY scheduled')] 
   elif path=='/api/staff/status' and self.command=='POST':
    b=c.execute('SELECT * FROM bookings WHERE id=?',(d['id'],)).fetchone();require(b and (b['status'],d['status']) in [('Pending','Dispatched'),('Dispatched','Delivered')],'Invalid status transition.');c.execute('UPDATE bookings SET status=? WHERE id=?',(d['status'],d['id']));result={'message':'Delivery updated.'}
   elif path=='/api/staff/assign' and self.command=='POST':
    b=c.execute('SELECT * FROM bookings WHERE id=?',(d['id'],)).fetchone();require(b and b['status']=='Pending','Only pending deliveries can be assigned.');require(c.execute('SELECT 1 FROM drivers WHERE id=? AND active=1',(d['driver_id'],)).fetchone(),'Active driver required.');require(not c.execute("SELECT 1 FROM bookings WHERE driver_id=? AND scheduled=? AND status IN ('Pending','Dispatched') AND id!=?",(d['driver_id'],b['scheduled'],b['id'])).fetchone(),'Driver already assigned at this time.');c.execute('UPDATE bookings SET driver_id=? WHERE id=?',(d['driver_id'],d['id']));result={'message':'Driver assigned.'}
   elif path=='/api/staff/save' and self.command=='POST':
    require(u['role']=='admin','Administrator required.');table=d['table'];require(table in ('drivers','tankers','settings'),'Invalid record.')
    fields={'drivers':['name','mobile','active'],'tankers':['plate','capacity','zone','price','driver_id','active'],'settings':['booking_limit']}[table]
    if table=='tankers':require(int(d['capacity'])>0 and int(d['price'])>=0 and d['zone'].strip(),'Valid capacity, price and zone required.')
    if table=='settings':require(1<=int(d['booking_limit'])<=100 and d['kind'] in ('residential','commercial'),'Limit must be 1–100.')
    if table=='drivers':require(d['name'].strip() and d['mobile'].strip(),'Driver name and mobile required.')
    vals=[d[k] for k in fields]
    if table=='settings':c.execute('UPDATE settings SET booking_limit=? WHERE kind=?',(d['booking_limit'],d['kind']))
    elif d.get('id'):c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',vals+[d['id']])
    else:c.execute('INSERT INTO '+table+'('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',vals)
    result={'message':'Record saved.'}
   else:self.respond({'error':'Not found'},404);return
   if path.startswith('/api/staff') and self.command=='POST':c.execute('INSERT INTO audit(actor,action) VALUES(?,?)',(u['id'],path+' '+json.dumps(d)))
   c.commit();self.respond(result,cookie=cookie)
  except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as e:c.rollback();self.respond({'error':str(e)},400)
  finally:c.close()
def main():
 p=argparse.ArgumentParser();p.add_argument('--seed',action='store_true');p.add_argument('--recover');p.add_argument('--port',type=int,default=8000);args=p.parse_args();init()
 if args.seed:
  with connect() as c:
   for role in ('admin','dispatcher','citizen'):
    c.execute('INSERT OR IGNORE INTO users(name,email,mobile,password,role) VALUES(?,?,?,?,?)',(role.title(),role+'@wtms.local',{'admin':'9000000001','dispatcher':'9000000002','citizen':'9000000003'}[role],password('DemoPass123!'),role))
   if not c.execute('SELECT 1 FROM drivers').fetchone():
    for i,zone in enumerate(('North','Central','South')):
     for size in (5000,10000):
      cur=c.execute('INSERT INTO drivers(name,mobile) VALUES(?,?)',(zone+' '+str(size)+' Driver','900001'+str(i)+str(size)));c.execute('INSERT INTO tankers(plate,capacity,zone,price,driver_id) VALUES(?,?,?,?,?)',('WT-'+str(cur.lastrowid).zfill(3),size,zone,750 if size==5000 else 1400,cur.lastrowid))
  print('Demo data ready. Demo password: DemoPass123!');return
 if args.recover:
  with connect() as c:
   u=c.execute('SELECT id FROM users WHERE email=?',(args.recover.lower(),)).fetchone();require(u,'Unknown email.');token=secrets.token_urlsafe(32);c.execute('INSERT INTO recovery VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],time.time()+900));print('Deliver this single-use token through a verified private channel (expires in 15 minutes): '+token)
  return
 print('WTMS running at http://localhost:'+str(args.port));ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
if __name__=='__main__':main()
