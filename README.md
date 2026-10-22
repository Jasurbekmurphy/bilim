# Bilimlar manbai

Texnikum pedagoglari tayyorlagan resurslar (prezentatsiya, ma'ruza matni, test va h.k.) saqlanadigan platforma.

## Imkoniyatlar

**Administrator**
- O'qituvchi va o'quvchilarga login/parol beradi: bittalab yoki Excel/CSV fayldan ko'plab.
- Parolni tiklaydi, akkauntni bloklaydi.
- Kafedra va mutaxassisliklarni qo'shadi. O'qituvchi o'zi yozgan mutaxassisliklarni tasdiqlaydi, nomini tuzatadi yoki mavjudi bilan birlashtiradi.
- Statistika: har bir foydalanuvchi qachon kirgan, necha marta kirgan, saytda jami va o'rtacha qancha vaqt bo'lgan, hozir kim saytda, kunlik kirishlar grafigi. Har bir foydalanuvchining kirishlar tarixi (vaqt, davomiylik, IP).
- Nomaqbul resursni yashiradi, izohlarni o'chiradi.

**O'qituvchi**
- Fayl yuklaydi (PDF, PowerPoint, Word, Excel, rasm): kafedra va mutaxassislikni tanlaydi, ro'yxatda bo'lmasa o'zi yozadi.
- Faylning birinchi beti avtomatik rasmga aylantiriladi va ro'yxatda ko'rinadi; PowerPoint/Word fayllarni brauzerda to'liq ko'rish ham mumkin.
- O'z resurslarini tahrirlaydi va o'chiradi.

**O'quvchi**
- Bosh sahifadan o'zi ro'yxatdan o'tadi (o'qituvchilarga login/parolni admin beradi).
- Profilda Telegramni ulaydi: parolni unutsa, tiklash kodi Telegram botga keladi.
- Kafedra, mutaxassislik, o'qituvchi bo'yicha saralaydi, nom bo'yicha qidiradi; eng yangi / eng yuqori reyting / eng ko'p ko'rilgan tartibida ko'radi.
- 1–5 baho qo'yadi, like bosadi, izoh yozadi, resursni "Saqlanganlar"ga qo'shadi, yuklab oladi.

**Reyting**
- Resurs balli = o'rtacha baho × 10 + like soni × 1 + hisobga o'tgan izohlar × 0.5.
- Bitta akkauntdan bitta resursga faqat birinchi izoh hisobga o'tadi, qolganlari oddiy izoh bo'lib qoladi. Muallifning o'z izohi, bahosi va like'i hisoblanmaydi.
- O'qituvchi reytingi uning barcha resurslari ballari yig'indisi ("O'qituvchilar reytingi" sahifasi).
- Koeffitsientlar `app.py` boshida (`SCORE_*`) o'zgartiriladi.

## Ishga tushirish

### Serverga o'rnatish (Contabo VPS, Ubuntu 22.04/24.04)

1. Contabo panelida VPS'ning IP manzili va root parolini oling.
2. Kompyuteringizdan serverga kiring: `ssh root@SERVER_IP`
3. Bitta buyruq bilan o'rnating:

```bash
curl -fsSL https://raw.githubusercontent.com/Jasurbekmurphy/bilim/main/deploy/install.sh | bash
```

Skript Docker'ni o'rnatadi, kodni `/opt/bilim` ga yuklab oladi, saytni ishga tushiradi va oxirida sayt manzili hamda admin parolini chiqaradi. Har kuni zaxira nusxa `/opt/bilim-backups` ga yoziladi (oxirgi 14 kun).

**Serverda boshqa saytlar (nginx) bo'lsa:** skript buni o'zi aniqlaydi, mavjud nginx'ga tegmaydi va platformani unga alohida `bilim.conf` fayli orqali ulaydi. Bu holatda domen yoki subdomen majburiy.

**Domen ulash (HTTPS bilan):** domenning DNS sozlamasida `A` yozuvini server IP'siga yo'naltiring, keyin:

```bash
bash /opt/bilim/deploy/install.sh bilim.texnikum.uz
```

HTTPS sertifikati avtomatik olinadi va yangilanadi.

**Yangilash:** GitHub'ga yangi kod qo'shilgandan keyin serverda `bash /opt/bilim/deploy/install.sh` ni qayta ishga tushiring. Ma'lumotlar saqlanib qoladi.

**Zaxiradan tiklash:** `cd /opt/bilim && docker compose down && rm -rf data && tar -xzf /opt/bilim-backups/bilim-SANA.tar.gz && docker compose up -d`

### Docker'siz

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py            # http://localhost:5000
```

PowerPoint/Word fayllarning birinchi betini ko'rsatish uchun kompyuterda LibreOffice o'rnatilgan bo'lishi kerak (`soffice` buyrug'i). PDF va rasmlar LibreOffice'siz ham ishlaydi.

### Birinchi kirish

Login `admin`, parol `ADMIN_PASSWORD` (berilmagan bo'lsa `admin123`). Birinchi kirishda parolni almashtirish so'raladi. Keyin "Kafedralar" sahifasida kafedra va mutaxassisliklarni, "Foydalanuvchilar" sahifasida o'qituvchi va o'quvchilarni qo'shing.

Excel'dan import uchun ustunlar tartibi: `login, F.I.Sh., rol, kafedra, guruh, parol` (oxirgi uchtasi ixtiyoriy).

### Telegram bot (parolni tiklash)

1. Telegramda [@BotFather](https://t.me/BotFather) ga `/newbot` yozing, botga nom bering (masalan `bilimbase_bot`) va bergan tokenni nusxalang.
2. Serverda `/opt/bilim/.env` faylida `TELEGRAM_BOT_TOKEN=` qatoriga tokenni yozing.
3. `bash /opt/bilim/deploy/install.sh` ni qayta ishga tushiring.

Shundan keyin foydalanuvchilar **Profil → Telegramni ulash** tugmasi orqali akkauntini botga ulaydi. Parolni unutsa, kirish sahifasidagi "Parolni unutdingizmi?" orqali 6 xonali kod Telegramga keladi (10 daqiqa amal qiladi, 5 ta urinish).

## Sozlamalar (muhit o'zgaruvchilari)

| O'zgaruvchi | Ma'nosi | Standart |
|---|---|---|
| `DATA_DIR` | Baza va fayllar papkasi | loyiha papkasi |
| `ADMIN_PASSWORD` | Birinchi admin paroli | `admin123` |
| `MAX_UPLOAD_MB` | Fayl hajmi chegarasi | `100` |
| `SECRET_KEY` | Sessiya kaliti | avtomatik yaratiladi |
| `TELEGRAM_BOT_TOKEN` | Parol tiklash boti tokeni | bo'sh (o'chiq) |
| `ALLOW_REGISTRATION` | O'quvchilar o'zi ro'yxatdan o'tishi (`0` = o'chiq) | `1` |
| `PORT` | `python app.py` uchun port | `5000` |

## Testlar

```bash
pip install pytest && python -m pytest -q tests
```
