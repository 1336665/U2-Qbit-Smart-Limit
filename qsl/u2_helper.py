#!/usr/bin/env python3
"""
qBit Smart Limit - U2 网页辅助模块
支持优惠抓取和Cookie检测
"""

import re
import time
import threading
from functools import reduce
from typing import Optional, Tuple
from datetime import datetime

import requests

from .utils import C, wall_time, get_logger

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


class U2WebHelper:
    def __init__(self, cookie: str, proxy: str = ""):
        self.cookie = cookie
        self.proxy = proxy
        self.session = requests.Session()
        self.session.headers['User-Agent'] = f'qBit-Smart-Limit/{C.VERSION}'
        self.cookies = {'nexusphp_u2': cookie} if cookie else {}
        self.enabled = bool(cookie) and BS4_AVAILABLE
        self._lock = threading.Lock()
        self._last_cookie_check = 0
        self._cookie_valid = True
    
    def close(self):
        if self.session:
            try: self.session.close()
            except: pass
    
    def _request(self, url: str, timeout: int = 15) -> Optional[str]:
        try:
            proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None
            resp = self.session.get(url, cookies=self.cookies, proxies=proxies, timeout=timeout)
            if resp.status_code == 200: return resp.text
        except Exception as e:
            get_logger().debug(f"请求失败 {url}: {e}")
        return None
    
    def check_cookie_valid(self) -> Tuple[bool, str]:
        """检查Cookie是否有效"""
        if not self.enabled:
            return False, "未配置Cookie"
        
        try:
            html = self._request('https://u2.dmhy.org/index.php', timeout=10)
            if not html:
                return False, "无法连接到U2"
            
            if 'logout.php' in html or '登出' in html or 'userdetails.php' in html:
                self._cookie_valid = True
                return True, "Cookie有效"
            else:
                self._cookie_valid = False
                return False, "Cookie已失效，请重新登录获取"
        except Exception as e:
            return False, f"检查失败: {e}"
    
    def is_cookie_valid(self) -> bool:
        return self._cookie_valid
    
    def search_tid_by_hash(self, torrent_hash: str) -> Optional[Tuple[int, Optional[float], str]]:
        if not self.enabled: return None
        try:
            url = f'https://u2.dmhy.org/torrents.php?search={torrent_hash}&search_area=5'
            html = self._request(url)
            if not html: return None
            
            with self._lock:
                soup = BeautifulSoup(html.replace('\n', ''), 'lxml')
                table = soup.select('table.torrents')
                if not table or len(table[0].contents) <= 1: return None
                
                row = table[0].contents[1]
                if not hasattr(row, 'contents') or len(row.contents) < 2: return None
                
                # 获取 TID
                try:
                    link = row.contents[1]
                    href = ""
                    if hasattr(link, 'find'):
                        a_tag = link.find('a')
                        href = a_tag.get('href', '') if a_tag else ''
                    
                    match = re.search(r'id=(\d+)', href)
                    if not match: return None
                    tid = int(match.group(1))
                except: return None

                # 获取发布时间
                publish_time = None
                try:
                    if len(row.contents) > 3:
                        time_cell = row.contents[3]
                        if hasattr(time_cell, 'find'):
                            time_elem = time_cell.find('time')
                            if time_elem:
                                date_str = time_elem.get('title') or time_elem.get_text(' ')
                                if date_str:
                                    dt = datetime.strptime(date_str.strip(), '%Y-%m-%d %H:%M:%S')
                                    publish_time = dt.timestamp()
                except: pass

                # 获取优惠信息
                promo_text = "无优惠"
                try:
                    promos = []
                    imgs = row.contents[1].find_all('img')
                    for img in imgs:
                        classes = img.get('class', [])
                        if not classes: continue
                        c_str = " ".join(classes) if isinstance(classes, list) else str(classes)
                        if 'pro_free2up' in c_str: promos.extend(['Free', '2x'])
                        elif 'pro_free' in c_str: promos.append('Free')
                        elif 'pro_2up' in c_str: promos.append('2x')
                        elif 'pro_50pct' in c_str: promos.append('50%')
                        elif 'pro_30pct' in c_str: promos.append('30%')
                        elif 'pro_custom' in c_str: promos.append('Custom')
                    
                    if promos:
                        promo_text = " + ".join(sorted(list(set(promos)), key=lambda x: len(x), reverse=True))
                except: pass

                get_logger().info(f"🔍 Hash {torrent_hash[:8]}... → tid {tid} | 优惠: {promo_text}")
                return (tid, publish_time, promo_text)

        except Exception as e:
            get_logger().debug(f"搜索 tid 失败: {e}")
            return None
    
    def get_peer_list_info(self, tid: int) -> Optional[dict]:
        if not self.enabled or not tid or tid < 0: return None
        try:
            url = f'https://u2.dmhy.org/viewpeerlist.php?id={tid}'
            html = self._request(url)
            if not html: return None
            
            with self._lock:
                soup = BeautifulSoup(html.replace('\n', ' '), 'lxml')
                tables = soup.find_all('table')
                result = {}
                
                for table in tables or []:
                    rows = table.find_all('tr')
                    for tr in rows:
                        if not tr.get('bgcolor'): continue
                        tds = tr.find_all('td')
                        if len(tds) < 2: continue
                        
                        try:
                            uploaded_str = tds[1].get_text(' ').strip()
                            if uploaded_str:
                                result['uploaded'] = self._parse_size(uploaded_str)
                        except: pass
                        
                        try:
                            if len(tds) > 10:
                                idle_str = tds[10].get_text(' ').strip()
                                if ':' in idle_str:
                                    parts = list(map(int, idle_str.split(':')))
                                    idle_seconds = reduce(lambda a, b: a * 60 + b, parts)
                                    result['last_announce'] = wall_time() - idle_seconds
                        except: pass
                        
                        if result: break
                    if result: break
                return result if result else None
        except: return None

    @staticmethod
    def _parse_size(size_str: str) -> int:
        try:
            parts = size_str.strip().split()
            if len(parts) != 2: return 0
            num = float(parts[0].replace(',', '.'))
            unit = parts[1]
            units = {'B': 0, 'KiB': 1, 'MiB': 2, 'GiB': 3, 'TiB': 4, 'PiB': 5}
            exp = units.get(unit, 0)
            return int(num * (1024 ** exp))
        except: return 0


def is_bs4_available() -> bool:
    return BS4_AVAILABLE
