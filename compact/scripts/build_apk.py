"""Build the Android app (assets/android/EmaraAI.apk): a small shell that opens the hub's Control Center.

    .venv\\Scripts\\python.exe scripts\\build_apk.py [--address https://my-pc.my-tailnet.ts.net:8797] [--sdk <folder with android-15 and android-34>]

Needs a JDK (javac, keytool) and Google's Android build-tools + one platform package, unpacked side by side:
    <sdk>\\android-15\\aapt2.exe, d8.bat, zipalign.exe, apksigner.bat      (build-tools_r35_windows.zip; r34 cannot convert classes built by JDK 21)
    <sdk>\\android-34\\android.jar                                         (platform-34-ext7_r03.zip)
No Gradle and no Android Studio. The signing key is made once and kept in data\\android\\ (keep it: updates must be signed with the same key).
"""
import argparse
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
from xml.sax.saxutils import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "android"


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(str(c) for c in cmd)[:300]}\n{(r.stdout + r.stderr)[-3000:]}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default="")
    ap.add_argument("--sdk", default=str(ROOT / ".tmp" / "android" / "sdk"))
    a = ap.parse_args()
    sdk = Path(a.sdk)
    bt, jar = sdk / "android-15", sdk / "android-34" / "android.jar"
    for need in (bt / "aapt2.exe", bt / "d8.bat", bt / "zipalign.exe", bt / "apksigner.bat", jar):
        if not need.exists():
            sys.exit(f"Missing {need}. See the top of this file for what to unpack where.")
    work = ROOT / ".tmp" / "android" / "build"
    if not work.resolve().is_relative_to(ROOT.resolve()):
        sys.exit("Android build folder resolves outside the workspace.")
    shutil.rmtree(work, ignore_errors=True)
    (work / "res" / "values").mkdir(parents=True)
    (work / "res" / "mipmap").mkdir(parents=True)
    address = a.address.strip()
    (work / "res" / "values" / "strings.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <string name="app_name">EmaraAI</string>\n'
        f'    <string name="default_address">{escape(address)}</string>\n</resources>\n', encoding="utf-8")
    shutil.copy(ROOT / "assets" / "emaraai.png", work / "res" / "mipmap" / "ic_launcher.png")

    run([bt / "aapt2.exe", "compile", "--dir", work / "res", "-o", work / "res.zip"])
    (work / "gen").mkdir()
    run([bt / "aapt2.exe", "link", "-o", work / "base.apk", "-I", jar, "--manifest", SRC / "AndroidManifest.xml", "--java", work / "gen",
         "--min-sdk-version", "24", "--target-sdk-version", "34", work / "res.zip"])
    sources = [*SRC.rglob("*.java"), *(work / "gen").rglob("*.java")]
    (work / "classes").mkdir()
    run(["javac", "-source", "8", "-target", "8", "-nowarn", "-Xlint:-options", "-cp", jar, "-d", work / "classes", *sources])
    classes = list((work / "classes").rglob("*.class"))
    (work / "dex").mkdir()
    run(["cmd", "/c", bt / "d8.bat", "--release", "--min-api", "24", "--lib", jar, "--output", work / "dex", *classes])
    with zipfile.ZipFile(work / "base.apk", "a", zipfile.ZIP_DEFLATED) as z:
        z.write(work / "dex" / "classes.dex", "classes.dex")
    run([bt / "zipalign.exe", "-f", "4", work / "base.apk", work / "aligned.apk"])

    keydir = ROOT / "data" / "android"
    keydir.mkdir(parents=True, exist_ok=True)
    store, secret = keydir / "emaraai-release.jks", keydir / "keystore-password.txt"
    if not store.exists():
        secret.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        signing_env = dict(os.environ, EMARAAI_SIGNING_PASSWORD=secret.read_text(encoding="utf-8").strip())
        run(["keytool", "-genkeypair", "-keystore", store, "-storepass:env", "EMARAAI_SIGNING_PASSWORD", "-keypass:env", "EMARAAI_SIGNING_PASSWORD",
             "-alias", "emaraai", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=EmaraAI Hub, O=EmaraAI"], env=signing_env)
    pw = secret.read_text(encoding="utf-8").strip()
    out = ROOT / "assets" / "android" / "EmaraAI.apk"
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["cmd", "/c", bt / "apksigner.bat", "sign", "--ks", store, "--ks-pass", "env:EMARAAI_SIGNING_PASSWORD", "--key-pass", "env:EMARAAI_SIGNING_PASSWORD", "--ks-key-alias", "emaraai",
         "--out", out, work / "aligned.apk"], env=dict(os.environ, EMARAAI_SIGNING_PASSWORD=pw))
    print(run(["cmd", "/c", bt / "apksigner.bat", "verify", "--verbose", "--print-certs", out])[:600])
    print(f"built {out}  ({out.stat().st_size // 1024} KB)  default address: {address or '(asked on first start)'}")


if __name__ == "__main__":
    main()
