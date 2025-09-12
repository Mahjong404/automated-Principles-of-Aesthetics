import os
import json

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.action_chains import ActionChains

import auto_sign_in
import auto_video
import auto_answer

#开始设置
options = webdriver.EdgeOptions()
options.add_experimental_option('excludeSwitches', ['enable-logging'])

#调用驱动启动浏览器
wd = webdriver.Edge(options=options, service=Service(r".\msedgedriver.exe"))

#自动登录
wd = auto_sign_in.sign_in(wd)

#观看视频
wd = auto_video.video(wd)

#自动做题
wd = auto_answer.answer(wd)

input("按回车退出")

wd.quit()