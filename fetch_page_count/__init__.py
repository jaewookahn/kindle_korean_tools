from calibre.customize import InterfaceActionBase


class FetchPageCountPlugin(InterfaceActionBase):
    name                    = 'Fetch Page Count'
    description             = '선택 도서의 페이지수를 Yes24 / Google Books에서 가져와 pages 컬럼에 저장'
    supported_platforms     = ['windows', 'osx', 'linux']
    author                  = 'Custom'
    version                 = (1, 4, 1)
    minimum_calibre_version = (5, 0, 0)
    actual_plugin           = 'calibre_plugins.fetch_page_count:FetchPageCountAction'


# ── InterfaceAction (GUI 초기화 후 로드됨) ────────────────────────────────────

from calibre.gui2.actions import InterfaceAction  # noqa: E402


class FetchPageCountAction(InterfaceAction):
    name        = 'Fetch Page Count'
    action_spec = ('페이지수 가져오기', 'book.png',
                   '선택 도서의 페이지수를 Yes24 / Google Books에서 가져옵니다', None)

    def genesis(self):
        self.qaction.triggered.connect(self.fetch_pages)

    # ── 설정 ──────────────────────────────────────────────────────────────────

    def _prefs(self):
        from calibre.utils.config import JSONConfig
        p = JSONConfig('plugins/fetch_page_count')
        p.defaults['google_key']   = ''
        p.defaults['pages_column'] = 'pages'
        return p

    def configure(self):
        try:
            from qt.core import (QDialog, QVBoxLayout, QLabel,
                                 QLineEdit, QDialogButtonBox)
        except ImportError:
            from PyQt5.Qt import (QDialog, QVBoxLayout, QLabel,
                                  QLineEdit, QDialogButtonBox)

        p = self._prefs()

        class _Dlg(QDialog):
            def __init__(self, parent):
                super().__init__(parent)
                self.setWindowTitle('페이지수 가져오기 — 설정')
                self.setMinimumWidth(420)
                lay = QVBoxLayout(self)
                lay.addWidget(QLabel(
                    'Google Books API 키 (선택 — 비워 두면 Yes24만 사용):'))
                self.gkey = QLineEdit(p['google_key'])
                self.gkey.setPlaceholderText('AIza...')
                lay.addWidget(self.gkey)
                lay.addWidget(QLabel('컬럼 이름 (# 없이):'))
                self.col = QLineEdit(p['pages_column'])
                lay.addWidget(self.col)
                bb = QDialogButtonBox(
                    QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                bb.accepted.connect(self.accept)
                bb.rejected.connect(self.reject)
                lay.addWidget(bb)

        d = _Dlg(self.gui)
        if d.exec_():
            p['google_key']   = d.gkey.text().strip()
            p['pages_column'] = d.col.text().strip() or 'pages'

    # ── 메인 ──────────────────────────────────────────────────────────────────

    def fetch_pages(self):
        try:
            from qt.core import QMenu
        except ImportError:
            from PyQt5.Qt import QMenu

        # 첫 실행 시 메뉴로 전환 (설정 항목 추가)
        if not hasattr(self, '_menu_built'):
            self._menu_built = True
            menu = QMenu(self.gui)
            menu.addAction('페이지수 가져오기').triggered.connect(self._run_fetch)
            menu.addSeparator()
            menu.addAction('설정...').triggered.connect(self.configure)
            self.qaction.setMenu(menu)

        self._run_fetch()

    def _selected_book_ids(self):
        view = self.gui.library_view
        sm   = view.selectionModel()
        rows = sm.selectedRows() if sm is not None else []
        m    = view.model()

        ids  = []
        seen = set()
        for r in rows:
            try:
                bid = m.id(r.row())
            except Exception:
                continue
            if bid is None or bid in seen:
                continue
            seen.add(bid)
            ids.append(bid)
        return ids

    def _run_fetch(self):
        import time

        try:
            from qt.core import QProgressDialog, Qt, QApplication
        except ImportError:
            from PyQt5.Qt import QProgressDialog, Qt, QApplication

        from calibre.gui2 import info_dialog, error_dialog

        book_ids = self._selected_book_ids()
        if not book_ids:
            info_dialog(self.gui, '페이지수 가져오기', '책을 먼저 선택하세요.', show=True)
            return

        p   = self._prefs()
        col = p['pages_column']
        db  = self.gui.current_db

        col_key = col if col.startswith('#') else f'#{col}'
        cfm = db.field_metadata.custom_field_metadata()
        if col_key not in cfm:
            error_dialog(
                self.gui, '컬럼 없음',
                f"사용자 정의 컬럼 '{col_key}'이 없습니다.\n"
                "'설정...'에서 컬럼 이름을 확인하세요.",
                show=True)
            return

        col_dtype = cfm[col_key].get('datatype')
        label = col.lstrip('#')
        gkey = p['google_key']
        _reset_session()
        pd  = QProgressDialog('준비 중...', '취소', 0, len(book_ids), self.gui)
        pd.setWindowTitle('페이지수 가져오기')
        pd.setWindowModality(Qt.WindowModal)
        pd.show()

        updated, not_found = [], []

        for i, book_id in enumerate(book_ids):
            if pd.wasCanceled():
                break
            mi = db.new_api.get_metadata(book_id)
            pd.setLabelText(f'({i+1}/{len(book_ids)}) {mi.title[:55]}')
            pd.setValue(i)
            QApplication.processEvents()

            reasons = set()
            pages, via = _get_pages(mi, gkey, reasons=reasons)
            if pages:
                val = str(pages) if col_dtype == 'text' else pages
                db.set_custom(book_id, val, label=label, commit=False)
                updated.append(f'{mi.title}  →  {pages}쪽  ({via})')
            else:
                not_found.append((mi.title, ', '.join(sorted(reasons))))

            time.sleep(0.4)

        db.commit()
        pd.setValue(len(book_ids))
        self.gui.library_view.refresh_grid()

        lines = [f'업데이트: {len(updated)}권  /  미발견: {len(not_found)}권', '']
        if updated:
            lines += ['[업데이트]'] + [f'  · {t}' for t in updated[:15]]
            if len(updated) > 15:
                lines.append(f'  ... 외 {len(updated)-15}권')
        if not_found:
            lines += ['', '[미발견]']
            for t, why in not_found[:15]:
                lines.append(f'  · {t}  —  {why}')
            if len(not_found) > 15:
                lines.append(f'  ... 외 {len(not_found)-15}권')
        info_dialog(self.gui, '완료', '\n'.join(lines), show=True)


# ── 페이지수 조회 ─────────────────────────────────────────────────────────────
#
# 소스:
#   Yes24   — 상품 페이지 '쪽수, 무게, 크기' 스크래핑 (키 불필요, 한글/외서 모두)
#   Google  — Books API. 설정에 API 키가 있을 때만 사용 (무인증 호출은 전 세계
#             공용 쿼터라 거의 항상 429). 키가 있으면 프로젝트별 일일 1000건
# 알라딘 OpenAPI 는 2026-10-30 서비스 종료 (errorCode 999) 로 제거.

def _has_hangul(s):
    if not s:
        return False
    for ch in s:
        if '가' <= ch <= '힣' or 'ᄀ' <= ch <= 'ᇿ' or '㄰' <= ch <= '㆏':
            return True
    return False


_SESSION = {'google_quota_dead': False}


def _reset_session():
    _SESSION['google_quota_dead'] = False


def _clean_title(title):
    """부제 제거: '오독_문학 비평의 실험' → '오독'."""
    import re
    t = re.split(r'\s*(?:_|:|：| - | — | – |\(|\[)\s*', title or '', maxsplit=1)[0].strip()
    return t if len(t) >= 2 else (title or '').strip()


def _norm(s):
    import re
    return re.sub(r'[\W_]+', '', (s or '').lower())


def _norm_pub(s):
    import re
    s = re.sub(r'주식회사|\(주\)|㈜|사단법인|재단법인|도서출판|출판사|출판', '', s or '')
    return _norm(s)


def _get_pages(mi, gkey='', reasons=None):
    """(페이지수, 출처) 반환. 실패 시 (0, '') 이고 reasons 에 소스별 사유 추가."""
    import re
    isbn      = re.sub(r'[^0-9X]', '', (mi.isbn or '').upper())
    title     = (mi.title or '').strip()
    authors   = [a for a in (mi.authors or []) if a and a != 'Unknown']
    publisher = (mi.publisher or '').strip()

    korean = _has_hangul(title) or any(_has_hangul(a) for a in authors)

    y24 = []
    if isbn:  y24.append(('Yes24', 'ISBN', lambda: _y24_isbn(isbn)))
    if title: y24.append(('Yes24', '제목', lambda: _y24_title(title, authors, publisher)))

    goo = []                     # 키 없으면 공용 쿼터라 거의 항상 429 → Yes24만 사용
    if gkey and isbn:
        goo.append(('Google', 'ISBN', lambda: _google(f'isbn:{isbn}', gkey)))
    if gkey and title:
        q = f'intitle:"{_clean_title(title)}"' + (f' inauthor:"{authors[0]}"' if authors else '')
        goo.append(('Google', '제목', lambda: _google(q, gkey)))

    steps = (y24 + goo) if korean else (goo + y24)
    if not steps:
        if reasons is not None: reasons.add('ISBN/제목 없음')
        return 0, ''

    errors = {}                      # src → 사유 (에러가 난 소스)
    for src, how, fn in steps:
        if src == 'Google' and _SESSION['google_quota_dead']:
            errors[src] = _google_quota_msg(gkey)
            continue
        try:
            p = fn()
            if p and 10 < p < 10000:
                return p, f'{src}·{how}'
        except Exception as e:
            code = getattr(e, 'code', None)
            if src == 'Google' and code == 429:
                _SESSION['google_quota_dead'] = True
                errors[src] = _google_quota_msg(gkey)
            elif src == 'Google' and code in (400, 403) and gkey:
                errors[src] = f'Google API 키 오류 ({code})'
            else:
                errors.setdefault(src, f'{src} 오류: {type(e).__name__}'
                                       + (f' {code}' if code else ''))

    if reasons is not None:
        for src in dict.fromkeys(s for s, _, _ in steps):
            reasons.add(errors.get(src, f'{src} 결과 없음'))
    return 0, ''


def _google_quota_msg(gkey):
    return 'Google 쿼터 초과' if gkey else 'Google 쿼터 초과 (설정에서 API 키 입력 필요)'


def _http(url, decode=False):
    import urllib.request
    req = urllib.request.Request(
        url, headers={'User-Agent': 'Mozilla/5.0 (compatible; Calibre/1.0)'})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
    return raw.decode('utf-8', errors='replace') if decode else raw


# ── Yes24 ────────────────────────────────────────────────────────────────────

_Y24 = 'https://www.yes24.com'


def _y24_items(query):
    """검색 결과 목록 → [{id, cat, name, auth, pub}]. 결과는 data-goods-no 로 식별."""
    import re, html, urllib.parse
    h = _http(f'{_Y24}/Product/Search?'
              + urllib.parse.urlencode({'domain': 'ALL', 'query': query}), decode=True)

    starts, seen = [], set()
    for m in re.finditer(r'data-goods-no="(\d+)"', h):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            starts.append((m.start(), m.group(1)))

    def txt(pat, seg):
        mm = re.search(pat, seg, re.S)
        return html.unescape(re.sub(r'<[^>]+>', '', mm.group(1))).strip() if mm else ''

    items = []
    for i, (pos, gid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else pos + 10000
        seg = h[pos:end]
        items.append({
            'id':   gid,
            'cat':  txt(r'class="gd_res">\[(.*?)\]', seg),
            'name': txt(r'class="gd_name"[^>]*>(.*?)</a>', seg),
            'auth': txt(r'class="authPub info_auth"[^>]*>(.*?)</span>', seg),
            'pub':  txt(r'class="authPub info_pub"[^>]*>(.*?)</span>', seg),
        })
    return items


def _y24_goods_pages(gid, follow_paper=True):
    """상품 페이지의 '쪽수, 무게, 크기' → 페이지수. eBook 이면 종이책 상품으로 이동."""
    import re
    h = _http(f'{_Y24}/Product/Goods/{gid}', decode=True)
    m = re.search(r'쪽수[^<]*</th>\s*<td[^>]*>\s*([\d,]+)\s*쪽', h)
    if m:
        return int(m.group(1).replace(',', ''))
    if follow_paper:
        pm = re.search(r'href="/product/goods/(\d+)"[^>]*formatLnk[^>]*>\s*종이책', h, re.I)
        if pm and pm.group(1) != str(gid):
            return _y24_goods_pages(pm.group(1), follow_paper=False)
    return 0


def _y24_isbn(isbn):
    for it in _y24_items(isbn)[:2]:
        p = _y24_goods_pages(it['id'])
        if p:
            return p
    return 0


# 파생판 — 원제에 없으면 감점
_Y24_DERIVED = ('young readers', 'summary', 'study guide', 'workbook', 'abridged',
                'graphic novel', 'audio', '요약', '축약', '청소년', '어린이', '만화',
                '큰글자', '오디오', '워크북')


def _y24_title(title, authors, publisher):
    """제목(+저자) 검색 → 제목 일치 후보를 출판사/저자로 점수화해 상위부터 조회."""
    t   = _clean_title(title)
    a   = authors[0] if authors else ''
    nt  = _norm(t)
    na  = _norm(a) if _has_hangul(a) else ''   # 외서는 Yes24 저자명이 한글 음차라 비교 무의미
    tl  = (title or '').lower()
    npb = _norm_pub(publisher)

    items = _y24_items(f'{t} {a}'.strip())
    if not items and a:
        items = _y24_items(t)

    cands = []
    for i, it in enumerate(items):
        if it['cat'] not in ('도서', '외서', '직수입양서', 'eBook', ''):
            continue
        name = _norm(it['name'])
        # 접두 일치만 인정 ('도리언 그레이의 초상' ↔ '도리언 그레이의 초상 1890').
        # 부분 포함은 'Volume' 같은 짧은 제목이 아무 데나 걸려 오탐.
        if len(name) < 2 or not (name.startswith(nt) or nt.startswith(name)):
            continue
        if '세트' in it['name'] and '세트' not in title:
            continue
        score = 0
        if name == nt:
            score += 2
        ip = _norm_pub(it['pub'])
        if npb and ip and (npb in ip or ip in npb):
            score += 3
        if na and na in _norm(it['auth']):
            score += 1
        if it['cat'] in ('도서', '외서', '직수입양서'):
            score += 1
        il = (it['name'] + ' ' + it['pub']).lower()
        if any(k in il and k not in tl for k in _Y24_DERIVED):
            score -= 3
        cands.append((-score, i, it))
    cands.sort(key=lambda c: (c[0], c[1]))

    for _, _, it in cands[:3]:
        p = _y24_goods_pages(it['id'])
        if p:
            return p
    return 0


# ── Google Books ─────────────────────────────────────────────────────────────

def _google(q, key=''):
    """상위 5건 중 pageCount 가 있는 첫 결과."""
    import json, urllib.parse
    params = {'q': q, 'maxResults': 5, 'fields': 'items/volumeInfo/pageCount'}
    if key:
        params['key'] = key
    d = json.loads(_http('https://www.googleapis.com/books/v1/volumes?'
                         + urllib.parse.urlencode(params)))
    for item in (d.get('items') or []):
        n = int(item.get('volumeInfo', {}).get('pageCount') or 0)
        if n:
            return n
    return 0
