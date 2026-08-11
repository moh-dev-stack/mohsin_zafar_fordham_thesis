"""Event-resource exposure under the L0/L1 linkage rungs. Generates results/linkage_exposure.json."""
import json

from dqa.linkage import SurrogateIssuer, rewrite_slice
from dqa.manifests import WIDTHS, load_width, project
from dqa.metrics import phi_exposed, phi_total
from dqa.run import DATASETS, MANIFESTS_DIR, read_cohort

DIMS=("completeness","plausibility","consistency","timeliness")
EVENTS=("Observation","Encounter")
out={"description":"Event-resource Safe Harbor exposure at linkage rungs L0 (reference) and L1 (surrogate). "
                   "Mean over event resources with at least one Safe Harbor occurrence.",
     "widths":list(WIDTHS),"cohorts":{}}
for name,d in DATASETS.items():
    cohort=read_cohort(d,200)
    per={}
    for w in WIDTHS:
        ms=load_width(MANIFESTS_DIR,w)
        for rung in ("reference","surrogate"):
            vals=[]
            issuer=SurrogateIssuer(seed=42)
            for r in cohort:
                if r.get("resourceType") not in EVENTS: continue
                slices=[]
                for dim in DIMS:
                    s=project(r,ms[dim])
                    if s: slices.append(rewrite_slice(s["_projected_fields"],issuer,rung) if rung!="reference" else s["_projected_fields"])
                t=phi_total(r)
                if t>0: vals.append(min(1.0,phi_exposed(slices)/t))
            per.setdefault(rung,{})[w]=round(sum(vals)/len(vals),4) if vals else None
            per.setdefault(rung+"_n",{})[w]=len(vals)
    out["cohorts"][name]=per
p="results/linkage_exposure.json"
with open(p,"w") as fh:
    fh.write(json.dumps(out,indent=2))
for n,v in out["cohorts"].items():
    print(f"  {n:<15} L0={v['reference']}  L1={v['surrogate']}  n={v['reference_n']}")
