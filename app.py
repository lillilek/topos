from flask import Flask, render_template, abort, request
import os
import markdown
import yaml
import datetime
import subprocess
import hmac
import hashlib

app = Flask(__name__)

# --- config ---
CONTENT_DIR = 'content/texts'
AUTHORS_DIR = 'content/authors'

GITHUB_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET_TOPOS", "your-secret")

# --- helpers ---
def load_markdown_file(path):
    with open(path, encoding='utf-8') as f:
        content = f.read()
        if content.startswith('---'):
            parts = content.split('---', 2)
            meta = yaml.safe_load(parts[1])
            body = markdown.markdown(parts[2])
            return meta, body
        return {}, markdown.markdown(content)

def load_all_texts():
    entries = []
    for root, dirs, files in os.walk(CONTENT_DIR):
        for filename in files:
            if filename.endswith('.md'):
                path = os.path.join(root, filename)
                meta, body = load_markdown_file(path)
                meta['content'] = body
                meta['slug'] = os.path.splitext(filename)[0]
                meta['metatags'] = meta.get('metatags', [])
                meta['tags'] = meta.get('tags', [])

                if 'date' in meta:
                    try:
                        meta['date_obj'] = datetime.datetime.strptime(meta['date'], '%Y-%m-%d').date()
                    except ValueError:
                        meta['date_obj'] = datetime.date(1900, 1, 1)  # на всякий случай

                entries.append(meta)
    # sort by date descending
    entries.sort(key=lambda x: x.get('date_obj', datetime.date(1900, 1, 1)), reverse=True)
    return entries

def load_authors():
    from collections import defaultdict

    authors = []

    # Словарь для хранения самой свежей даты по каждому автору
    latest_by_author = defaultdict(lambda: datetime.date(1900, 1, 1))

    # 1. Сканируем все тексты и собираем даты по author_slug
    for root, dirs, files in os.walk(CONTENT_DIR):
        for filename in files:
            if filename.endswith('.md'):
                path = os.path.join(root, filename)
                meta, _ = load_markdown_file(path)
                if 'author_slug' in meta and 'date' in meta:
                    try:
                        date_obj = datetime.datetime.strptime(meta['date'], '%Y-%m-%d').date()
                        slug = meta['author_slug']
                        if date_obj > latest_by_author[slug]:
                            latest_by_author[slug] = date_obj
                    except Exception:
                        continue

    # 2. Загружаем всех авторов и добавляем slug + latest_date
    for filename in os.listdir(AUTHORS_DIR):
        if filename.endswith('.yaml'):
            with open(os.path.join(AUTHORS_DIR, filename), encoding='utf-8') as f:
                data = yaml.safe_load(f)
                slug = os.path.splitext(filename)[0]
                data['slug'] = slug
                data['latest_date'] = latest_by_author[slug]
                
                if 'about_file' in data:
                    about_path = os.path.join(AUTHORS_DIR, data['about_file'])
                    if os.path.exists(about_path):
                        with open(about_path, encoding='utf-8') as af:
                            about_md = af.read()
                            data['about'] = markdown.markdown(about_md)
                
                authors.append(data)

    # 3. Сортировка по дате убыванию
    authors.sort(key=lambda a: a['latest_date'], reverse=True)

    return authors

@app.context_processor
def inject_now():
    return {'now': datetime.datetime.now()}

# --- routes ---
@app.route('/')
def index():
    texts = load_all_texts()
    return render_template('index.html', texts=texts[:5])

@app.route('/metatag/<metatag>')
def texts_by_metatag(metatag):
    all_texts = [t for t in load_all_texts() if metatag in t.get('metatags', [])]
    
    # --- Pagination ---
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1

    per_page = 10
    total = len(all_texts)
    total_pages = max((total - 1) // per_page + 1, 1)
    start = (page - 1) * per_page
    end = start + per_page
    texts = all_texts[start:end]

    return render_template(
        'texts_by_metatag.html',
        metatag=metatag,
        texts=texts,
        page=page,
        total_pages=total_pages
    )

@app.route('/authors')
def authors():
    authors = load_authors()
    return render_template('authors.html', authors=authors)

@app.route('/author/<slug>')
def author_page(slug):
    authors = load_authors()
    author = next((a for a in authors if a['slug'] == slug), None)
    if not author:
        abort(404)
    texts = [t for t in load_all_texts() if t.get('author_slug') == slug]
    return render_template('author.html', author=author, texts=texts)

@app.route('/text/<slug>')
def text_page(slug):
    texts = load_all_texts()
    for t in texts:
        if t['slug'] == slug:
            return render_template('text.html', text=t)
    abort(404)

@app.route('/contacts')
def contacts():
    return render_template('contacts.html')

@app.route('/donate')
def donate():
    return render_template('donate.html')


@app.route("/deploy", methods=["POST"])
def github_webhook():
    print("deploying....")
    signature = request.headers.get("X-Hub-Signature-256")
    if signature is None:
        abort(403)

    sha_name, signature = signature.split('=')
    if sha_name != 'sha256':
        abort(501)

    mac = hmac.new(GITHUB_SECRET.encode(), msg=request.data, digestmod=hashlib.sha256)

    if not hmac.compare_digest(mac.hexdigest(), signature):
        abort(403)

    # Do the git pull
    subprocess.run(["git", "pull"])
    subprocess.run(["systemctl", "restart", "topos"])
    return "OK", 200

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=3033)
