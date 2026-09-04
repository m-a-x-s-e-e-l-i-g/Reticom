plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val reticomTransportHost = providers.gradleProperty("reticomTransportHost").orNull?.trim().orEmpty()
val reticomTransportPort = providers.gradleProperty("reticomTransportPort").orNull?.toIntOrNull() ?: 4242

android {
    namespace = "com.retium.field"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.retium.field"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "RETICOM_TRANSPORT_HOST", "\"${reticomTransportHost.replace("\\", "\\\\").replace("\"", "\\\"")}\"")
        buildConfigField("int", "RETICOM_TRANSPORT_PORT", reticomTransportPort.toString())

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        System.getenv("RETIUM_BUILD_PYTHON")?.let { buildPython(it) }
        pip {
            install("rns==1.5.2")
            install("lxmf==1.1.1")
            install("cryptography==42.0.8")
            install("pyserial==3.5")
            install("fastapi==0.99.1")
            install("pydantic==1.10.13")
            install("uvicorn==0.23.2")
            install("wsproto==1.2.0")
            install("qrcode==8.2")
        }
        pyc {
            src = true
            pip = true
        }
        extractPackages("retium")
    }
    sourceSets {
        getByName("main") {
            srcDir("../../src")
            srcDir("src/main/python")
        }
    }
}

dependencies {
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
}
