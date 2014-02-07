ARC=PointIm-plugin.zip

zip: config_dialog.ui  __init__.py manifest.ini  plugin.py  pointim.png  pointim_tag_button.png  README.md  unknown.png
	rm -f $(ARC)
	rm -rf pointim
	mkdir pointim
	cp $? pointim/
	zip -r $(ARC) pointim/
	rm -rf pointim
	
