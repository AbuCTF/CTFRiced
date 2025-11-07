Ricing CTFd to the best of my abilities!

CTFd [Latest](https://github.com/CTFd/CTFd/releases/latest)

2025 Infra [`on the pot`]
- 3.7.7 [Tag](https://github.com/AbuCTF/CTFRiced/releases/tag/3.7.7) <- fully tested
  - multiple plugin support + custom ui
    - docker challenges
    - anti cheat
- 3.8.0 [`cooked`]
  - discord notifier
  - geoint support
  - fractal theme (without fractals lol)


#### **Instructions**

```
git clone https://github.com/AbuCTF/CTFRiced.git
docker compose up --build
```
then paste `theme-header.css` and `theme-footer.js` in [localhost](https//localhost:8000/admin/config)

to take it one step further and customize landing page (optional)
```
docker exec -it ctfd-db-1 bash
mysql -u ctfd -p
```
password is `ctfd` (unless you explicitly changed it in the docker-compose.yml file)
```mysql
use ctfd;
UPDATE pages SET content = '<div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; min-height: 100vh; text-align: center; padding: 20px; box-sizing: border-box; overflow: hidden; transform: translateY(-30px);"><img src="/themes/core/static/img/logo.png" alt="CTF Banner" style="max-width: 300px; width: 100%; margin-bottom: 25px; transition: transform 0.2s ease, opacity 0.2s ease; cursor: pointer;" onmouseover="this.style.transform=''scale(1.05)''; this.style.opacity=''0.9''" onmouseout="this.style.transform=''scale(1)''; this.style.opacity=''1''" /><div id="countdown" style="font-family: Courier !important; color: #ff3333 !important; font-size: 1.5rem !important; margin-bottom: 35px !important; display: block !important; visibility: visible !important; opacity: 1 !important;">Loading...</div><div style="display: flex; justify-content: center; align-items: center; gap: 20px; flex-wrap: wrap;"><a href="/challenges" style="background: #1e3a3a !important; color: #64ffda !important; padding: 14px 20px !important; text-decoration: none !important; border: 2px solid #64ffda !important; border-radius: 4px !important; font-weight: 600 !important; display: flex !important; justify-content: center !important; align-items: center !important; width: 140px !important; height: 20px !important; box-sizing: border-box !important; font-size: 13px !important; white-space: nowrap !important; transition: all 0.15s ease !important; letter-spacing: 1px !important; text-transform: uppercase !important; box-shadow: 0 2px 4px rgba(0,0,0,0.3) !important; font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif !important;" onmouseover="this.style.background=''#2a4a4a''; this.style.boxShadow=''0 4px 8px rgba(0,0,0,0.4)''; this.style.transform=''translateY(-1px)''" onmouseout="this.style.background=''#1e3a3a''; this.style.boxShadow=''0 2px 4px rgba(0,0,0,0.3)''; this.style.transform=''translateY(0)''" onmousedown="this.style.transform=''translateY(1px) scale(0.98)''; this.style.boxShadow=''0 1px 2px rgba(0,0,0,0.4)''" onmouseup="this.style.transform=''translateY(-1px)''; this.style.boxShadow=''0 4px 8px rgba(0,0,0,0.4)''">ENTER ARENA</a><a href="/register" style="background: #3a1e1e !important; color: #ff6b6b !important; padding: 14px 20px !important; text-decoration: none !important; border: 2px solid #ff6b6b !important; border-radius: 4px !important; font-weight: 600 !important; display: flex !important; justify-content: center !important; align-items: center !important; width: 140px !important; height: 20px !important; box-sizing: border-box !important; font-size: 13px !important; white-space: nowrap !important; transition: all 0.15s ease !important; letter-spacing: 1px !
    '> important; text-transform: uppercase !important; box-shadow: 0 2px 4px rgba(0,0,0,0.3) !important; font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif !important;" onmouseover="this.style.background=''#4a2a2a''; this.style.boxShadow=''0 4px 8px rgba(0,0,0,0.4)''; this.style.transform=''translateY(-1px)''" onmouseout="this.style.background=''#3a1e1e''; this.style.boxShadow=''0 2px 4px rgba(0,0,0,0.3)''; this.style.transform=''translateY(0)''" onmousedown="this.style.transform=''translateY(1px) scale(0.98)''; this.style.boxShadow=''0 1px 2px rgba(0,0,0,0.4)''" onmouseup="this.style.transform=''translateY(-1px)''; this.style.boxShadow=''0 4px 8px rgba(0,0,0,0.4)''">JOIN NOW</a><a href="https://discord.com/invite/3ZaFbQRY3C" target="_blank" style="background: #2e1a3a !important; color: #c084fc !important; padding: 14px 20px !important; text-decoration: none !important; border: 2px solid #c084fc !important; border-radius: 4px !important; font-weight: 600 !important; display: flex !important; justify-content: center !important; align-items: center !important; width: 140px !important; height: 20px !important; box-sizing: border-box !important; font-size: 13px !important; white-space: nowrap !important; transition: all 0.15s ease !important; letter-spacing: 1px !important; text-transform: uppercase !important; box-shadow: 0 2px 4px rgba(0,0,0,0.3) !important; font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif !important;" onmouseover="this.style.background=''#3e2a4a''; this.style.boxShadow=''0 4px 8px rgba(0,0,0,0.4)''; this.style.transform=''translateY(-1px)''" onmouseout="this.style.background=''#2e1a3a''; this.style.boxShadow=''0 2px 4px rgba(0,0,0,0.3)''; this.style.transform=''translateY(0)''" onmousedown="this.style.transform=''translateY(1px) scale(0.98)''; this.style.boxShadow=''0 1px 2px rgba(0,0,0,0.4)''" onmouseup="this.style.transform=''translateY(-1px)''; this.style.boxShadow=''0 4px 8px rgba(0,0,0,0.4)''">DISCORD</a></div></div><style>body { overflow: hidden !important; margin: 0 !important; padding: 0 !important; } html { overflow: hidden !important; } .navbar, .footer, nav, footer { display: none !important; }</style><script>setTimeout(function(){var e=document.getElementById("countdown");if(e){var t=new Date("2025-10-18T09:00:00+05:30"),n=new Date("2025-12-19T21:00:00+05:30");function a(){var o=new Date;if(o>=n)return void(e.textContent="ENDED");if(o>=t)return void(e.textContent="LIVE");var d=t-o,i=Math.floor(d/864e5),r=Math.floor(d%864e5/36e5),s=Math.floor(d%36e5/6e4),c=Math.floor(d%6e4/1e3);e.textContent=i+"D "+r+"H "+s+"M "+c+"S"}a(),setInterval(a,1e3)}},500);</script>' WHERE id = 1;
```
- for database backups
```
docker exec ctfriced-db-1 mysqldump -u root -pctfd ctfd > backup.sql
```
quick fun fact: `mysqldump`
 is the utility that generates SQL queries from a running database.

- to restore DB
```
docker exec -i <repo>-db-1 mariadb -u root -p ctfd < backup.sql
```

#### Post Credits
H7CTF 2024 Infrastructure [writeup](https://abu.h7tex.com/docs/docs/dev/h7ctfinfra/)
