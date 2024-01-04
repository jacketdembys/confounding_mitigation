#!/bin/bash
scale_start=0
scale_increment=1
scale_end=18

# Delete jobs for each value in the range of neurons, layers, and scale
for (( scale=scale_start; scale<=scale_end; scale+=scale_increment )); do
    job_name="exp-svm-semg-${scale}"
    kubectl delete job $job_name
done