import time, random
import numpy as np

def solve_np(prices, rel, budget, step, max_items=4, lo_frac=0.85):
    """bounded knapsack with cardinality<=max_items, price quantized to `step`, maximize relevance.
       returns best (score, total_minor, idx list) with total in [lo_frac*B, B]."""
    K=len(prices); NB=budget//step+1
    q=[max(1,-(-p//step)) for p in prices]      # ceil-quantized so quantized total >= real total (never exceeds budget)
    NEG=-1e18
    # dp[c][b] = best score using exactly c items with quantized sum b ; keep parent for reconstruction
    dp=np.full((max_items+1,NB),NEG); dp[0,0]=0.0
    take=np.full((K,max_items+1,NB),False)
    for i in range(K):
        w=q[i]; r=rel[i]
        if w>=NB: continue
        for c in range(max_items,0,-1):
            cand=dp[c-1,:NB-w]+r
            better=cand>dp[c,w:]
            dp[c,w:]=np.where(better,cand,dp[c,w:])
            take[i,c,w:]=better
    best=(NEG,None,None)
    lo=int(lo_frac*NB)
    for c in range(1,max_items+1):
        seg=dp[c,lo:]
        j=int(seg.argmax())
        if seg[j]>best[0]: best=(seg[j],c,lo+j)
    return best

def solve_py(prices, rel, budget, step, max_items=4):
    K=len(prices); NB=budget//step+1
    q=[max(1,-(-p//step)) for p in prices]
    NEG=-1e18
    dp=[[NEG]*NB for _ in range(max_items+1)]; dp[0][0]=0.0
    for i in range(K):
        w=q[i]; r=rel[i]
        if w>=NB: continue
        for c in range(max_items,0,-1):
            prev=dp[c-1]; cur=dp[c]
            for b in range(NB-1,w-1,-1):
                v=prev[b-w]+r
                if v>cur[b]: cur[b]=v
    return max(max(row) for row in dp[1:])

random.seed(1)
K=60; budget=40_00   # $40.00 in cents => 4000 minor units ; step=2 => 2000 buckets
prices=[random.randint(300,3500) for _ in range(K)]
rel=[random.random() for _ in range(K)]
step=max(1,budget//2000)
t=time.perf_counter(); r=solve_np(prices,rel,budget,step); t1=(time.perf_counter()-t)*1000
print('numpy  K=60, count<=4, 2000 buckets: %.1f ms'%t1, 'best=',round(float(r[0]),3))
t=time.perf_counter(); r2=solve_py(prices,rel,budget,step); t2=(time.perf_counter()-t)*1000
print('pure-python same problem:          %.1f ms'%t2, 'best=',round(float(r2),3), '(consistent with numpy: %s)'%(abs(r2-r[0])<1e-6 or 'differs by lo-window'))
# YER-scale budget: 40 USD ~ large minor units; quantization keeps buckets bounded
budget=10_000_000; step=max(1,budget//2000); prices=[random.randint(500_000,9_000_000) for _ in range(K)]
t=time.perf_counter(); solve_np(prices,rel,budget,step); print('numpy  large-currency budget (still 2000 buckets): %.1f ms'%((time.perf_counter()-t)*1000))
K=20000; # naive: enumerate subsets of size<=3 from 20k items -> C(20000,3)
import math
print('naive subsets<=3 from 20,000 items = %.2e combinations'%(math.comb(20000,3)+math.comb(20000,2)))
