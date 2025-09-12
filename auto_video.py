from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.edge.webdriver import WebDriver

def video(wd : WebDriver) -> WebDriver:
    watch = wd

    #切换到内层iframe
    watch.switch_to.frame('iframe')
    watch.switch_to.frame(watch.find_element(By.TAG_NAME, "iframe"))

    #开始播放
    play_button = watch.find_element(By.CLASS_NAME, "vjs-big-play-button")
    play_button.click()
    ActionChains(watch).move_to_element(watch.find_element(By.CLASS_NAME, "vjs-play-control")).perform()

    #等待播放完成
    watch.implicitly_wait(1750)
    watch.find_element(By.CLASS_NAME, "vjs-ended")

    #点击“下一节”按钮，跳转到习题部分
    watch.switch_to.default_content()
    watch.find_element(By.ID, "prevNextFocusNext").click()

    #重置等待时间
    watch.implicitly_wait(10)
    return watch