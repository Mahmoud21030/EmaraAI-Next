# EmaraAI Next — ابدأ من هنا

## على جهازك (Windows)
1. نزّل الريبو (Code → Download ZIP) وفكّه، أو `git clone`.
2. دبل كليك على **`Setup.cmd`**.
   - بيسطّب لوحده أي حاجة ناقصة: Python و Git و Tailscale.
   - بيسألك 3 أسئلة، وكل سؤال ليه إجابة جاهزة: اسم الجهاز، وفين يتعمل backup للكود (اختياري)، وفين يتعمل backup للملفات (بيلاقي Google Drive أو OneDrive لوحده).
   - بيخلي البرنامج يفتح مع Windows، ويفتحلك الصفحة في المتصفح.
3. خلاص 🎉 الصفحة على `http://127.0.0.1:8810`.

## تدخل عليه من بره (الموبايل أو اللابتوب)
- سطّب Tailscale على الموبايل وسجّل دخول بنفس الحساب.
- افتح العنوان اللي `Setup.cmd` طبعه، وشكله كده: `https://<اسم-جهازك>.<tailnet>.ts.net`.
- مفيش أي port مفتوح على الإنترنت. أجهزتك اللي في Tailscale بس هي اللي تقدر توصل.
- جهازك بيتسجّل في Tailscale **مرة واحدة بس**. لو شغّلت `Setup.cmd` تاني ميعملش جهاز جديد، والـ workflows والـ runners عمرها ما بتدخل Tailscale.
- عايز تتحكم في الجهاز كله (Remote Desktop)؟ لو Windows عندك Pro، فعّل Remote Desktop من الإعدادات، وادخل من أي جهاز في Tailscale باسم جهازك.

## عايز تغيّر حاجة؟
شغّل `Setup.cmd` تاني. الإجابات القديمة هتبقى هي الإجابات الجاهزة، فتغيّر اللي عايزه بس.

## أماكن تانية
Linux أو VPS: `sh next/scripts/install.sh`. Docker: `cd next && docker compose up -d`. التفاصيل في `next/README.md`.
