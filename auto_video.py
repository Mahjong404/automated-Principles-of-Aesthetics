from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.edge.webdriver import WebDriver

def video(wd : WebDriver) -> WebDriver:
    watch = wd
    watch.switch_to.frame('iframe')
    watch.switch_to.frame(watch.find_element(By.TAG_NAME, "iframe"))
    play_button = watch.find_element(By.CLASS_NAME, "vjs-big-play-button")
    play_button.click()
    ActionChains(watch).move_to_element(watch.find_element(By.CLASS_NAME, "vjs-play-control")).perform()
    watch.implicitly_wait(1750)
    watch.find_element(By.CLASS_NAME, "vjs-ended")
    return watch