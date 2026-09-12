pipeline {
  agent { label 'mac-mini' }
  options { disableConcurrentBuilds(); timeout(time: 70, unit: 'MINUTES') }
  parameters {
    choice(name: 'MODE', choices: ['SETUP', 'ABORT', 'DIAGNOSTIC'], description: 'Manager-authorized mode')
    string(name: 'REQUEST_ID', trim: true, description: 'Bound immutable request identity')
    string(name: 'SOURCE_SHA', trim: true, description: 'Immutable Git source SHA')
    string(name: 'ADMISSION_RECEIPT', trim: true, description: 'Bounded venue admission receipt')
  }
  stages {
    stage('Fail closed before checkout') {
      steps {
        script {
          if (!(params.REQUEST_ID ==~ /[A-Za-z0-9._-]{1,128}/) ||
              !(params.SOURCE_SHA ==~ /[0-9a-f]{40}/) ||
              !params.ADMISSION_RECEIPT?.trim()) {
            error('missing or malformed immutable request/admission identity')
          }
          if (!(params.MODE in ['SETUP', 'ABORT', 'DIAGNOSTIC'])) error('unsupported mode')
          if (params.MODE == 'DIAGNOSTIC') error('diagnostic execution is manager-owned and disabled in this candidate')
        }
      }
    }
    stage('Non-pytest probes only') {
      when { expression { params.MODE in ['SETUP', 'ABORT'] } }
      steps { echo "${params.MODE} receipt for ${params.REQUEST_ID}; pytest_exit=null" }
    }
  }
  post { always { archiveArtifacts artifacts: 'artifacts/**', allowEmptyArchive: true } }
}
