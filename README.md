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
- Kafedra, mutaxassislik, o'qituvchi bo'yicha saralaydi, nom bo'yicha qidiradi; eng yangi / eng yuqori reyting / eng ko'p ko'rilgan tartibida ko'radi.
- 1–5 baho qo'yadi, like bosadi, izoh yozadi, resursni "Saqlanganlar"ga qo'shadi, yuklab oladi.

**Reyting**
- Resurs balli = o'rtacha baho × 10 + like soni × 1 + hisobga o'tgan izohlar × 0.5.
- Bitta akkauntdan bitta resursga faqat birinchi izoh hisobga o'tadi, qolganlari oddiy izoh bo'lib qoladi. Muallifning o'z izohi, bahosi va like'i hisoblanmaydi.
- O'qituvchi reytingi uning barcha resurslari ballari yig'indisi ("O'qituvchilar reytingi" sahifasi).
- Koeffitsientlar `app.py` boshida (`SCORE_*`) o'zgartiriladi.

## Ishga tushirish

### Docker bilan (server uchun tavsiya etiladi)

```bash
ADMIN_PASSWORD=kuchli-parol docker compose up -d --build
```

Sayt `http://SERVER_IP:8000` manzilida ochiladi. Barcha ma'lumotlar (baza, fayllar) `./data` papkasida saqlanadi, shu papkadan zaxira nusxa oling.

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

## Sozlamalar (muhit o'zgaruvchilari)

| O'zgaruvchi | Ma'nosi | Standart |
|---|---|---|
| `DATA_DIR` | Baza va fayllar papkasi | loyiha papkasi |
| `ADMIN_PASSWORD` | Birinchi admin paroli | `admin123` |
| `MAX_UPLOAD_MB` | Fayl hajmi chegarasi | `100` |
| `SECRET_KEY` | Sessiya kaliti | avtomatik yaratiladi |
| `PORT` | `python app.py` uchun port | `5000` |

## Testlar

```bash
pip install pytest && python -m pytest -q tests
```
