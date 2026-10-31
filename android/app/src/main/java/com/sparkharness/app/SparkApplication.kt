package com.sparkharness.app

import android.app.Application
import com.sparkharness.app.util.AppContext

class SparkApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        AppContext.init(this)
    }
}
