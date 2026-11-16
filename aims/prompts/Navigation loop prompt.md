# Navigation loop prompt

DO NOT run any tools in parallel. DO NOT stop the experiment in middle! Run all the tools following the order of the prompt:
1. Use -3e-6m as the low limit, 3e-6m as the high limit, 500nm tip lift, approach and lift the tip. Wait until this process is done.
2. Start scan with series name "sampleLocating", 128 pixels and 40lines. Wait until the scan is completed.
3. Check controller status. If the controller status is off, then repeat step 1 to 3 with 100nm more tip lift. Wait until this is done.
4. Retract the tip. Wait until the tip is retracted.
5. Get the sxm "sampleLocating" with the latest id, resize to 256x256 and save it to the same directory of the .sxm image. Wait until this is done.
6. Perform position prediction on the image, consider the sxm image size is 53x53 microns. Wait until this is done.
7. If the uncertainty predicted in step 6 is larger than 53um in x or y, run GP regression on the sxm "sampleLocating" with the latest id, using the same file name with "_GP" as the output name, then repeat step 6 on this new image. Wait until this is done.
8. Calculate the number of steps need for both x and y directions using the prediction with the smallest uncertainty. Wait until this is done.
9. If the predicted distance from center is larger than 50e-6m, use 50V and 100Hz for both x and y, move the tip with the predicted number of steps. Wait until this is done.
10. Repeat step 1 to 9 until the predicted distance from center is smaller than 50e-6m.
