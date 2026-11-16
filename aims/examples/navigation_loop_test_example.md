# Navigation Loop

> **DO NOT run any tools in parallel. DO NOT stop the experiment in middle!**
> Run all the tools following the order of the prompt:

1. Load template image `templates\ArrowMarker_10x.jpg`. The size of the image is 1433x953um, with resolution 1636x1088. Use (867, 498) as the center
2. Get `examples\SampleLocatingImages\Image1.sxm`. Resize to 256x256 and save it to the same directory of the .sxm image. Wait until this is done.
3. Perform position prediction on the image, consider the image size is 53x53 microns. Wait until this is done.
4. If the predicted position has x or y uncertainty larger than 53um, run GP regression on the same sxm, using the same file name with "_GP" as the output name, then repeat step 3 on this new image. Wait until this is done.
5. Repeat step 2 to 4 on Image2 to Image6. Wait until this is done.
6. Plot all predicted positions on the template image.
