
import subprocess,sys
def test_missing_spec(tmp_path):
 p=subprocess.run([sys.executable,"-m","verify_and_repair.z3verify",str(tmp_path/"no.py")],capture_output=True,text=True)
 assert p.returncode==2 and "not_applicable" in p.stdout
