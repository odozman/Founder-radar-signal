import requests
from datetime import datetime, timezone

class SlackClient:
    def __init__(self, token, channel): self.token=token; self.channel=channel
    def send(self,item):
        if not self.token or not self.channel: return {'sent':False,'reason':'Slack credentials not configured'}
        head='🔥 *EARLY YC SIGNAL — Founder Announced Before YC*' if item.get('type')=='early' else ('🚀 *NEW SPEEDRUN COMPANY*' if item.get('program')=='Speedrun' else '🟢 *NEW YC COMPANY*')
        text=f"{head}\n\n*Company:* {item.get('company','Unknown company')}\n*Founder:* {item.get('founder','Not listed')}\n*Batch:* {item.get('batch','Unspecified')}\n*Source:* {item.get('source','Unknown')}\n*Status:* {item.get('status','Signal detected')}\n\n*Description:* {item.get('description','Not available')[:900]}\n*Original post / profile:* {item.get('url','')}\n*Detected:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        r=requests.post('https://slack.com/api/chat.postMessage',headers={'Authorization':f'Bearer {self.token}','Content-Type':'application/json; charset=utf-8'},json={'channel':self.channel,'text':text},timeout=20); r.raise_for_status(); data=r.json()
        if not data.get('ok'): raise RuntimeError(data.get('error','Slack API error'))
        return {'sent':True,'ts':data.get('ts','')}
