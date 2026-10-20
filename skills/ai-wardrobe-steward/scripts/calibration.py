#!/usr/bin/env python3
"""Offer one optional, situational calibration question at a time."""
import argparse, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--feels-like-f",type=float,required=True); ap.add_argument("--wind-mph",type=float,default=0)
    ap.add_argument("--outdoor-minutes",type=int,default=15); ap.add_argument("--activity",choices=["sedentary","light","active"],default="light")
    ap.add_argument("--rain",action="store_true"); ap.add_argument("--context",default="general"); ap.add_argument("--answer",choices=["shirt-only","light-layer","jacket","sweater-and-jacket"])
    args=ap.parse_args(); base=["--feels-like-f",str(args.feels_like_f),"--wind-mph",str(args.wind_mph),"--outdoor-minutes",str(args.outdoor_minutes),"--activity",args.activity,"--context",args.context]
    if args.rain: base.append("--rain")
    cmd=[sys.executable,str(HERE/'comfort_profile.py'),"observe" if args.answer else "predict",*base]
    if args.answer: cmd += ["--preference",args.answer]
    r=subprocess.run(cmd,capture_output=True,text=True); print(r.stdout.strip()); raise SystemExit(r.returncode)


if __name__ == "__main__": main()
