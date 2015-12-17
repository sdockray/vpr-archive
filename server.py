# -*- coding: utf-8 -*-
import os, sys
import cherrypy
from datetime import datetime, timedelta
#from mutagen.mp3 import MP3
from tinytag import TinyTag
from ConfigParser import SafeConfigParser
from pymongo import MongoClient
import pymongo
from bson.objectid import ObjectId
from subprocess import Popen, PIPE

default_config = {
        'rootdir':'/lockers/sean_vol/radio/aaaaarchive/',
        'mp3_archives': '/path/to/dir1,/another/path/to/somewhere',
        'port': 8086,
        'path':'radio/memory',
        'mongo_server': 'localhost',
        'mongo_port': 27017,
        'mongo_db': 'radio',
        'stream_url': 'http://abc.com/icecast/archive/',
        'ingest_dir': '/some/ingest/path',
        'upload_dir': '/some/upload/path'
}

def reload_template():
        global html
        try:
                with open('template.html', 'r') as f:
                        html = f.read()
        except:
                html = """
                <html>
                        <head>
                        <title>radio station</title>
                        <style>#messages {padding:5px;background-color:#ffff99;} </style>
                        </head>
                        <body>
                        <ul>%s</ul>
                        </body>
                </html>
                """

def get_mp3_list():
        for mp3_archive in MP3_ARCHIVES:
                for filename in os.listdir(mp3_archive):
                        if filename.endswith('.ogg') or filename.endswith('.mp3'):
                                yield mp3_archive, filename


# Loads a single entry
def db_get(value, key='_id'):
        global db
        if key=='_id':
                value = ObjectId(value)
        return db.mp3s.find_one({key:value})

# Returns all the mp3s stored in the db 
def db_get_all():
        global db
        data = {}
        mp3s = db.mp3s.find().sort('date', pymongo.DESCENDING)
        for mp3 in mp3s:
                data[mp3['fullpath']] = mp3
        return data

# Inserts a record
def db_insert(data):
        global db
        return db.mp3s.insert_one(data)

# Updates a record
def db_update(key, data):
        global db
        print key, data
        db.mp3s.update_one({'_id':ObjectId(key)}, {'$set': data})

# Deletes a record from db
def db_remove(key):
        global db
        db.mp3s.remove({'fullpath': key})

# Looks at all the files available and makes sure there are corresponding entries in the db
def db_check():
        mp3s = get_mp3_list()
        for d, mp3 in mp3s:
                fullpath = os.path.join(d,mp3)
                data = db_get(fullpath, 'fullpath')
                if not data:
                        print 'adding: ', fullpath
                        data = {
                                'fullpath': fullpath,
                                'title': mp3,
                                'description': '',
                        }
                        try:
                                date_part = mp3.split('.')[0]
                                date_obj = datetime.strptime(date_part, '%Y-%m-%d_%H-%M-%S')
                                #data['date'] = datetime.strftime(date_obj, '%a %b %d, %Y at %I:%M %p')
                                data['date'] = date_obj
                        except:
                                date_obj = datetime.fromtimestamp(os.path.getctime(fullpath))
                                #data['date'] = datetime.strftime(date_obj, '%a %b %d, %Y at %I:%M %p')
                                data['date'] = date_obj
                        tag = TinyTag.get(fullpath)
                        m, s = divmod(tag.duration, 60)
                        h, m = divmod(m, 60)
                        data['duration'] = "%d:%02d:%02d" % (h, m, s)
                        db_insert(data)
        delete = []
        for fullpath in db_get_all():
                if not os.path.exists(fullpath):
                        delete.append(fullpath)
        for d in delete:
                print 'removing: ', d
                db_remove(d)


def mp3_info(id):
        info = db_get(id)
        print info
        if not info:
                raise cherrypy.HTTPRedirect(cherrypy.request.base)
        return info

def save_mp3_info(mp3, title, description):
        db_update( mp3, {
                'title': title.encode('utf-8').strip(), 
                'description':description.encode('utf-8').strip()
        })

def render_html(mp3s, show_edit_links=False):
        global SERVER_PATH, STREAM_URL, ROOT_DIR
        mp3s_html = ''
        for fullpath in mp3s:
                mp3 = fullpath.replace(ROOT_DIR, '')
                info = mp3s[fullpath]
                a = '<a href="%s%s.m3u">%s</a> %s (%s)' % (STREAM_URL, mp3, info['title'], info['date'], info['duration'])
                if show_edit_links:
                        a = "%s <a href='%sedit?id=%s'>edit</a>" % (a, SERVER_PATH, info['_id'])
                if info['description']:
                        a = "%s<br/>\n<em>%s</em>" % (a, info['description'])
                mp3s_html = "%s<li class='list-group-item'>%s</li>\n" % (mp3s_html, a)
        return html % mp3s_html


class Station(object):
        
        @cherrypy.expose
        def default(self, *args, **kwargs):
                mp3s = db_get_all()
                return render_html(mp3s)

        @cherrypy.expose
        def changetitles(self):
                mp3s = db_get_all()
                return render_html(mp3s, True)
        
        @cherrypy.expose
        def ingest(self):
                global SERVER_PATH
                content = """
                <script type="text/javascript">
                function BeginProcess() {
                        document.getElementById('trigger').disabled = true;
                        document.getElementById('messages').innerHTML = 'starting<br/>';
                        var iframe = document.createElement("iframe");
                        iframe.src = "%singester?url="+document.getElementById("url").value;
                        iframe.style.display = "none";
                        document.body.appendChild(iframe);
                }
                function UpdateProgress(message)
                {
                        document.getElementById('messages').innerHTML += message+"<br/>";
                }
                </script>
                <label for="url">url (youtube, vimeo, soundcloud)</label>
                <input id="url" value="" name="url" class="form-control"/><br/>
                <input type="submit" class="btn btn-default" value="Grab audio" id="trigger" onclick="BeginProcess(); return false;" />
                <p><div id="messages">progress...<br/></div></p>
                """ % SERVER_PATH
                return html % content
        
        @cherrypy.expose
        def ingester(self, url):
                global INGEST_DIR
                def format_update(x):
                        return """
                        <script>parent.UpdateProgress("%s");</script>
                        """ % x
                def ingest(url):
                        command = "youtube-dl -o '%s/%s' --restrict-filenames --extract-audio --audio-format mp3 %s" % (INGEST_DIR, '%(title)s-%(id)s.%(ext)s', url)
                        process = Popen(command, shell=True, stdout=PIPE, stderr=PIPE, close_fds=True, preexec_fn=os.setsid)
                        while True:
                                line = process.stdout.readline()
                                if line != '':
                                        yield format_update(line.rstrip().replace(INGEST_DIR, ''))
                                else:
                                        break
                        db_check()
                return ingest(url)

        ingester._cp_config = {'response.stream': True}

        @cherrypy.expose
        def upload(self):
                return html % """
                <form action="uploader" method="post" enctype="multipart/form-data">
                    <label for="f">filename</label>
                    <input type="file" name="f" /><br />
                    <input type="submit" class="btn btn-default"/>
                </form>
                """


        @cherrypy.expose
        def uploader(self, f):
                global UPLOAD_DIR
                size = 0
                if not str(f.content_type) == 'audio/mpeg':
                        return "Sorry, I only know how to handle mp3s!"
                with open(UPLOAD_DIR + '/' + f.filename, 'w') as o:
                        while True:
                            data = f.file.read(8192)
                            if not data:
                                break
                            o.write(data)
                            size += len(data)
                db_check()
                content = "Upload successful: name=%s (%s bytes)" % (f.filename, size)
                return html % content

        @cherrypy.expose
        def edit(self, id=None):
                global SERVER_PATH
                if id is None:
                        return "???"
                mp3 = id
                info = mp3_info(mp3)
                content = """
                        <html><body>
                        <form method='get' action='%sposted'>
                        <input type="hidden" name="mp3" value="%s"/>
                        title:<br/>
                        <input value="%s" name="title" class="form-control"/><br/>
                        description:<br/>
                        <textarea name="description" class="form-control"/>%s</textarea><br/>
                        <input type='submit' value='Submit' class="btn btn-default"/>
                        </form></body>
                        </html>
                """ % (SERVER_PATH, mp3, info['title'],info['description'])
                return html % content

        @cherrypy.expose
        def posted(self, mp3, title, description):
                save_mp3_info(mp3, title, description)
                content = "saved! <a href='%s'>back to the list</a>" % SERVER_PATH
                return html % content

        @cherrypy.expose
        def rebuild(self):
                db_check()
                reload_template()
                return html % "db check complete"



def init_db():
        global db
        client = MongoClient(MONGO_SERVER, MONGO_PORT)
        db = client[MONGO_DB]


def load_config():
        global SERVER_PORT, SERVER_PATH, ROOT_DIR, MP3_ARCHIVES, MONGO_SERVER, MONGO_PORT, MONGO_DB, STREAM_URL, INGEST_DIR, UPLOAD_DIR
        # Load config
        config = SafeConfigParser(default_config)
        # Create empty config file
        if not os.path.exists('config.ini'):
                print "Creating a config.ini file. Edit it to match your server settings."
                with open('config.ini','w') as f:
                        f.write("[config]\n")
                        for k in default_config:
                                f.write("%s: %s\n" % (k, default_config[k]))
                sys.exit()
        # Try reading the config file
        try:
                config.read('config.ini')
        except:
                print "Create a config.ini file to set directories & port"

        # Set constants from config file or defaults
        SERVER_PORT = int(config.get('config', 'port'))
        SERVER_PATH =   config.get('config', 'path')    
        ROOT_DIR = config.get('config', 'rootdir')
        MP3_ARCHIVES = config.get('config', 'mp3_archives').split(',')
        MONGO_SERVER = config.get('config', 'mongo_server')
        MONGO_PORT = int(config.get('config', 'mongo_port'))
        MONGO_DB = config.get('config', 'mongo_db')
        STREAM_URL = config.get('config', 'stream_url')
        INGEST_DIR = config.get('config', 'ingest_dir')
        UPLOAD_DIR = config.get('config', 'upload_dir')

# UWSGI application
def application(environ, start_response):
        load_config()
        init_db()
        db_check()
        reload_template()
        cherrypy.config.update({
                'server.socket_port': SERVER_PORT,
        })
        cherrypy.tree.mount(PdfServer(), '/%s/' % SERVER_PATH)
        return cherrypy.tree(environ, start_response)

# Starting things up
if __name__ == '__main__':
        try:
                load_config()
                init_db()
                db_check()
                reload_template()
                conf = {}
                cherrypy.config.update({
                        'server.socket_port': SERVER_PORT,
                })
                app = cherrypy.tree.mount(Station(), SERVER_PATH)
                cherrypy.quickstart(app, config=conf)
        except:
                print "Station couldn't start :("                        