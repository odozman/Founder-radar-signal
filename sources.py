import re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
UA='FounderRadarSignal/1.1'
YC_URL='https://www.ycombinator.com/companies'
SPEEDRUN_URL='https://speedrun.a16z.com/companies'
YC_RE=re.compile(r'\bYC\s*[SWPFX]\d{2}\b',re.I)

def clean(s): return re.sub(r'\s+',' ',s or '').strip()
def get(url):
    r=requests.get(url,timeout=30,headers={'User-Agent':UA}); r.raise_for_status(); return r

def fetch_yc_directory():
    soup=BeautifulSoup(get(YC_URL).text,'html.parser'); out={}
    for a in soup.select('a[href^="/companies/"]'):
        slug=a.get('href','').rstrip('/').split('/')[-1]
        if not slug or slug in {'companies','industry'}: continue
        context=clean(a.parent.get_text(' ',strip=True) if a.parent else a.get_text(' ',strip=True))[:1500]; m=YC_RE.search(context)
        out[slug]={'identity':'yc:'+slug,'name':clean(a.get_text(' ',strip=True)) or slug.replace('-',' ').title(),'program':'YC','batch':m.group(0).upper().replace(' ','') if m else 'Unspecified','url':'https://www.ycombinator.com'+a['href'],'description':context,'founder':''}
    return list(out.values())

def fetch_speedrun_directory():
    soup=BeautifulSoup(get(SPEEDRUN_URL).text,'html.parser'); out={}
    for a in soup.select('a[href^="/companies/"]'):
        slug=a.get('href','').rstrip('/').split('/')[-1]
        if not slug: continue
        context=clean(a.parent.get_text(' ',strip=True) if a.parent else a.get_text(' ',strip=True))[:1800]
        out[slug]={'identity':'speedrun:'+slug,'name':clean(a.get_text(' ',strip=True)) or slug.replace('-',' ').title(),'program':'Speedrun','batch':'Unspecified','url':'https://speedrun.a16z.com'+a['href'],'description':context,'founder':''}
    return list(out.values())

YC_PATTERNS=[r'\bYC\s*[SWPFX]\d{2}\b',r'\bY Combinator\b',r'\bgot into YC\b',r'\baccepted into Y Combinator\b',r'\bjoining Y Combinator\b']
SPEED_PATTERNS=[r'\ba16z\s+Speedrun\b',r'\bSpeedrun\s+(?:batch|cohort)\b']

def serper(key,q,n=20):
    r=requests.post('https://google.serper.dev/search',headers={'X-API-KEY':key,'Content-Type':'application/json'},json={'q':q,'num':n},timeout=30); r.raise_for_status(); return r.json().get('organic',[])

def extract_company(text,url):
    for p in [r'(?:building|launching|launched|introducing|meet)\s+([A-Z][A-Za-z0-9&.\' -]{1,60}?)(?:\s+\(YC|\s+is\s+|\s+—|[.!?,]|$)',r'\b([A-Z][A-Za-z0-9&.\' -]{1,60})\s*\(YC\s*[SWPFX]\d{2}\)']:
        m=re.search(p,text,re.I)
        if m and 1<len(clean(m.group(1)))<70: return clean(m.group(1)).strip(' .,-–')
    path=urlparse(url).path.strip('/').split('/')
    return path[1].replace('-',' ').title() if len(path)>1 and path[0]=='company' else 'Unknown company'

def social_signals(source,key):
    if source=='X': qs=['site:x.com ("YC S26" OR "YC W26" OR "got into YC" OR "accepted into Y Combinator" OR "joining Y Combinator")','site:x.com ("a16z Speedrun" OR "Speedrun batch" OR "Speedrun cohort")']; hosts=('x.com','twitter.com')
    else: qs=['site:linkedin.com/posts ("YC S26" OR "YC W26" OR "got into YC" OR "accepted into Y Combinator" OR "joining Y Combinator")','site:linkedin.com/posts ("a16z Speedrun" OR "Speedrun batch" OR "Speedrun cohort")','site:linkedin.com/company ("YC S26" OR "Y Combinator" OR "a16z Speedrun")']; hosts=('linkedin.com',)
    out={}
    for q in qs:
        for r in serper(key,q):
            url=r.get('link',''); host=urlparse(url).netloc.lower()
            if not any(h in host for h in hosts): continue
            text=clean(r.get('title','')+' '+r.get('snippet','')); yc=any(re.search(p,text,re.I) for p in YC_PATTERNS); speed=any(re.search(p,text,re.I) for p in SPEED_PATTERNS)
            if not(yc or speed): continue
            m=YC_RE.search(text); out[url]={'source':source,'program':'Speedrun' if speed and not yc else 'YC','company':extract_company(text,url),'founder':clean(r.get('title','').split(' - ')[0]) or 'Unknown founder','batch':m.group(0).upper().replace(' ','') if m else 'Unspecified','description':r.get('snippet','')[:900],'url':url}
    return list(out.values())

def official_social_hit(signal,key):
    company=clean(signal.get('company',''))
    if company=='Unknown company': return False
    q=f'site:x.com/ycombinator "{company}" OR site:linkedin.com/company/y-combinator "{company}"' if signal['program']=='YC' else f'site:x.com/a16z "{company}" OR site:linkedin.com/company/a16z "{company}"'
    return any(company.lower() in clean(h.get('title','')+' '+h.get('snippet','')).lower() for h in serper(key,q,10))
