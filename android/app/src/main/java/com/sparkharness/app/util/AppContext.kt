package com.sparkharness.app.util

import android.app.Application
import android.content.Context

/** 全局 Application Context 持有者，供 Prefs 等无界面组件使用。 */
object AppContext {
    private lateinit var app: Application
    fun init(a: Application) { app = a }
    fun get(): Context = app.applicationContext
}
